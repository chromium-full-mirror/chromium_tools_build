# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process
from recipe_engine import turboci

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc
from RECIPE_MODULES.build.chromium_tests import api as chromium_tests
from RECIPE_MODULES.build.chromium_tests_builder_config import try_spec
from RECIPE_MODULES.build.code_coverage.api import MAX_CANDIDATE_FILES

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.go.chromium.org.luci.resultdb.proto.v1 \
    import common as resultdb_common
from PB.go.chromium.org.luci.resultdb.proto.v1 \
    import test_result as test_result_pb2
from PB.go.chromium.org.luci.analysis.proto.v1 import test_history
from PB.recipe_modules.build.chromium_compilator.properties import InputProperties

from PB.turboci.data.build.v1.build_check_options import (
    BuildCheckOptions,
    Product,
)
from PB.turboci.data.build.v1.build_check_results import BuildCheckResult
from PB.turboci.data.chrome.build.v1.analyze_options import AnalyzeOptions
from PB.turboci.data.chrome.build.v1.analyze_results import AnalyzeResults
from PB.turboci.data.chrome.build.v1.compile_targets_options import (
    CompileTargetsOptions)
from PB.turboci.graph.orchestrator.v1.check_kind import CheckKind
from PB.turboci.graph.orchestrator.v1.check_state import CheckState
from PB.turboci.graph.orchestrator.v1.workplan import WorkPlan

DEPS = [
    'chromium',
    'chromium_compilator',
    'chromium_tests',
    'chromium_tests_builder_config',
    'code_coverage',
    'depot_tools/tryserver',
    'filter',
    'flakiness',
    'recipe_engine/buildbucket',
    'recipe_engine/cv',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/luci_analysis',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/resultdb',
    'recipe_engine/runtime',
    'recipe_engine/step',
    'recipe_engine/swarming',
    'test_utils',
]

PROPERTIES = InputProperties


def RunSteps(api, properties):
  return api.chromium_compilator.compilator_steps(properties)


def GenTests(api):
  _TEST_BUILDERS = ctbc.BuilderDatabase.create({
      'chromium.test': {
          'chromium-rel':
              ctbc.BuilderSpec.create(
                  chromium_config='chromium',
                  gclient_config='chromium',
              ),
      },
  })

  _TEST_TRYBOTS = ctbc.TryDatabase.create({
      'tryserver.chromium.test': {
          'rts-rel':
              ctbc.TrySpec.create(
                  mirrors=[
                      ctbc.TryMirror.create(
                          builder_group='chromium.test',
                          buildername='chromium-rel',
                          tester='chromium-rel',
                      ),
                  ],
                  regression_test_selection=try_spec.ALWAYS,
              ),
      }
  })

  def override_test_spec():
    return api.chromium_tests.read_targets_spec(
        'fake-group', {
            'fake-builder': {
                'scripts': [{
                    "isolate_profile_data": True,
                    "name": "check_static_initializers",
                    "script": "check_static_initializers.py",
                    "test_id_prefix": "ninja://check_static_initializers/"
                }],
            },
            'fake-tester': {
                'gtest_tests': [{
                    'name': 'browser_tests',
                    'swarming': {},
                }],
                'isolated_scripts': [{
                    'test': 'angle_unittests',
                    'name': 'angle_unittests',
                    'swarming': {},
                }, {
                    'test': 'angle_unittests_no_swarm',
                    'name': 'angle_unittests_no_swarm',
                }],
            },
        })

  ctbc_api = api.chromium_tests_builder_config

  def ctbc_properties(builder_spec=None):
    return ctbc_api.properties(
        ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
            builder_group='fake-group',
            builder='fake-builder',
            builder_spec=builder_spec,
        ).with_mirrored_tester(
            builder_group='fake-group',
            builder='fake-tester',
        ).assemble())

  def basic_assert_workplan(assert_, workplan: WorkPlan):
    analyze_check_id = f'{chromium_tests.BUILD_CHECK_ID} analyze'
    check_ids = set(c.identifier.id for c in workplan.checks)
    if not assert_(
        check_ids == {
            chromium_tests.SOURCE_CHECK_ID,
            chromium_tests.BUILD_CHECK_ID,
            analyze_check_id,
        }):
      return  # pragma: no cover

    # source check verifications ###############################################
    # The source check is created by bot_update, just verify it's of appropriate
    # kind and is final, none of the other details since the check isn't being
    # read by the recipe code
    source_check = turboci.get_check_by_short_id(workplan,
                                                 chromium_tests.SOURCE_CHECK_ID)
    assert_(source_check.kind == CheckKind.CHECK_KIND_SOURCE)
    assert_(source_check.state == CheckState.CHECK_STATE_FINAL)

    # build check verifications ################################################
    build_check = turboci.get_check_by_short_id(workplan,
                                                chromium_tests.BUILD_CHECK_ID)
    assert_(build_check.kind == CheckKind.CHECK_KIND_BUILD)
    assert_(build_check.state == CheckState.CHECK_STATE_FINAL)

    # Verify that the build check depends on the source and analyze checks
    build_check_deps = {
        edge.check.identifier.id for edge in build_check.dependencies.edges
    }
    assert_(
        build_check_deps == {chromium_tests.SOURCE_CHECK_ID, analyze_check_id})

    # Verify the options on the build check
    build_check_options = turboci.get_option(BuildCheckOptions, build_check)
    expected_build_check_options = BuildCheckOptions(
        target=BuildCheckOptions.BuildTarget(
            name='chromium/try/fake-orchestrator',
            product=Product.PRODUCT_BROWSER,
        ))
    assert_(build_check_options == expected_build_check_options)

    compile_targets_options = turboci.get_option(CompileTargetsOptions,
                                                 build_check)
    expected_compile_targets_options = CompileTargetsOptions(compile_targets=[
        'angle_unittests',
        'angle_unittests_no_swarm',
        'browser_tests',
        'infra/orchestrator:orchestrator_all',
    ])
    assert_(compile_targets_options == expected_compile_targets_options)

    # Verify the results on the build check
    build_check_results = turboci.get_results(BuildCheckResult, build_check)
    expected_build_check_results = [BuildCheckResult(success=True)]
    assert_(build_check_results == expected_build_check_results)

    # analyze check verifications ##############################################
    analyze_check = turboci.get_check_by_short_id(workplan, analyze_check_id)
    assert_(analyze_check.kind == CheckKind.CHECK_KIND_ANALYSIS)
    assert_(analyze_check.state == CheckState.CHECK_STATE_FINAL)

    # Verify the options on the analyze check
    analyze_options = turboci.get_option(AnalyzeOptions, analyze_check)
    expected_analyze_options = AnalyzeOptions(
        test_targets=[
            'angle_unittests', 'angle_unittests_no_swarm', 'browser_tests'
        ],
        analyze_config_path='testing/buildbot/trybot_analyze_config.json',
        analyze_config_names=['chromium', 'linux'],
    )
    assert_(analyze_options == expected_analyze_options)

    # Verify the results on the analyze check
    analyze_results = turboci.get_results(AnalyzeResults, analyze_check)
    expected_analyze_results = [
        AnalyzeResults(
            compile_targets=[
                'angle_unittests', 'angle_unittests_no_swarm', 'browser_tests'
            ],
            test_targets=[
                'angle_unittests', 'angle_unittests_no_swarm', 'browser_tests'
            ],
        ),
    ]
    assert_(analyze_results == expected_analyze_results)

  yield api.test(
      'basic',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-compilator',
          revision='deadbeef',
      ),
      api.platform.name('linux'),
      api.path.exists(
          api.path.checkout_dir.joinpath('out', '666d-fake-compilator',
                                         'browser_tests')),
      ctbc_properties(),
      api.properties(
          InputProperties(
              orchestrator=InputProperties.Orchestrator(
                  builder_name='fake-orchestrator',
                  builder_group='fake-try-group'))),
      override_test_spec(),
      api.post_process(post_process.StepTextContains, 'report builders', [
          "running tester 'fake-tester' on group 'fake-group' against "
          "builder 'fake-builder' on group 'fake-group'"
      ]),
      api.post_process(post_process.StepCommandContains, 'bot_update',
                       ['--patch_ref']),
      api.post_process(post_process.MustRun, 'compile (with patch)'),
      api.post_process(
          post_process.StepCommandContains,
          'compile (with patch)',
          ['infra/orchestrator:orchestrator_all'],
      ),
      api.post_process(post_process.MustRun, 'isolate tests (with patch)'),
      api.post_process(post_process.MustRun, 'swarming trigger properties'),
      api.post_process(post_process.DoesNotRun, 'skylab trigger properties'),
      api.post_check(lambda check, steps: check(
          steps['post output trigger properties'].tags[
              'chromium.outputted_trigger_properties'] ==
          ('outputted swarming and/or skylab trigger properties'))),
      api.post_process(post_process.MustRun,
                       'check_static_initializers (with patch)'),
      api.post_process(post_process.MustRun,
                       'angle_unittests_no_swarm (with patch)'),
      api.post_process(post_process.DoesNotRun, 'angle_unittests (with patch)'),
      api.post_process(post_process.DropExpectation),
      api.assert_workplan(basic_assert_workplan),
  )

  yield api.test(
      'triggered-directly',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-compilator',
          revision='deadbeef',
      ),
      api.post_check(post_process.SummaryMarkdownRE,
                     'it is an error to trigger it directly'),
      api.post_process(post_process.DropExpectation),
      api.expect_status('INFRA_FAILURE'),
  )

  SKYLAB_ISOLATE_TEXT = """
    {'variables': {'command': ['bin/run_lacros_smoke_tast_tests',
                            '--logs-dir=${ISOLATED_OUTDIR}'],
                'files': ['../../.vpython',
                          'bin/run_vaapi_unittest',
                          'resources.pak',
                          'resources.pak.info',
                          './chrome',
                          '../../testing/buildbot/filters',
                          'gen/third_party',
                          '../../testing/buildbot/filters',
                          'bin/lacros_fyi_tast_tests.filter'
                            ]}}
  """

  yield api.test(
      'skylab_and_swarmed_tests',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-compilator',
          revision='deadbeef',
      ),
      api.platform.name('linux'),
      api.path.exists(
          api.path.cache_dir.joinpath(
              'builder/src/out/666d-fake-compilator/lacros_all_tast_tests.isolate'
          ),
          api.path.start_dir.joinpath('squashfs', 'squashfs-tools',
                                      'mksquashfs'),
      ),
      ctbc_properties(
          builder_spec=ctbc.BuilderSpec.create(
              gclient_config='chromium',
              chromium_config='chromium',
              skylab_gs_bucket='chrome-test-builds',
          ),),
      api.properties(
          InputProperties(
              orchestrator=InputProperties.Orchestrator(
                  builder_name='fake-orchestrator',
                  builder_group='fake-try-group'))),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'scripts': [{
                      "isolate_profile_data": True,
                      "name": "check_static_initializers",
                      "script": "check_static_initializers.py",
                      "test_id_prefix": "ninja://check_static_initializers/"
                  }],
              },
              'fake-tester': {
                  'gtest_tests': [{
                      'name': 'browser_tests',
                      'swarming': {},
                  }],
                  'skylab_tests': [{
                      'name': 'lacros_all_tast_tests',
                      'cros_board': 'volteer',
                      'autotest_name': 'chromium',
                  }],
              },
          }),
      api.step_data(
          'prepare skylab tests.collect runtime deps for '
          'lacros_all_tast_tests.read isolate file',
          api.file.read_text(SKYLAB_ISOLATE_TEXT)),
      api.post_process(post_process.StepTextContains, 'report builders', [
          "running tester 'fake-tester' on group 'fake-group' against "
          "builder 'fake-builder' on group 'fake-group'"
      ]),
      api.post_process(post_process.StepCommandContains, 'bot_update',
                       ['--patch_ref']),
      api.post_process(post_process.MustRun, 'compile (with patch)'),
      api.post_process(
          post_process.StepCommandContains,
          'compile (with patch)',
          ['infra/orchestrator:orchestrator_all'],
      ),
      api.post_process(post_process.MustRun, 'isolate tests (with patch)'),
      api.post_process(post_process.MustRun, 'prepare skylab tests'),
      api.post_process(post_process.MustRun, 'swarming trigger properties'),
      api.post_process(post_process.MustRun, 'skylab trigger properties'),
      api.post_process(post_process.MustRun,
                       'check_static_initializers (with patch)'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'only_skylab_tests',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-compilator',
          revision='deadbeef',
      ),
      api.path.exists(
          api.path.cache_dir.joinpath(
              'builder/src/out/666d-fake-compilator/lacros_all_tast_tests.isolate'
          ),
          api.path.start_dir.joinpath('squashfs', 'squashfs-tools',
                                      'mksquashfs'),
      ),
      ctbc_properties(
          builder_spec=ctbc.BuilderSpec.create(
              gclient_config='chromium',
              chromium_config='chromium',
              skylab_gs_bucket='chrome-test-builds',
          ),),
      api.properties(
          InputProperties(
              orchestrator=InputProperties.Orchestrator(
                  builder_name='fake-orchestrator',
                  builder_group='fake-try-group'))),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'scripts': [{
                      "isolate_profile_data": True,
                      "name": "check_static_initializers",
                      "script": "check_static_initializers.py",
                      "test_id_prefix": "ninja://check_static_initializers/"
                  }],
              },
              'fake-tester': {
                  'skylab_tests': [{
                      'name': 'lacros_all_tast_tests',
                      'cros_board': 'volteer',
                      'autotest_name': 'chromium',
                  }],
              },
          }),
      api.step_data(
          'prepare skylab tests.collect runtime deps for '
          'lacros_all_tast_tests.read isolate file',
          api.file.read_text(SKYLAB_ISOLATE_TEXT)),
      api.post_process(post_process.StepTextContains, 'report builders', [
          "running tester 'fake-tester' on group 'fake-group' against "
          "builder 'fake-builder' on group 'fake-group'"
      ]),
      api.post_process(post_process.StepCommandContains, 'bot_update',
                       ['--patch_ref']),
      api.post_process(post_process.MustRun, 'compile (with patch)'),
      api.post_process(
          post_process.StepCommandContains,
          'compile (with patch)',
          ['infra/orchestrator:orchestrator_all'],
      ),
      api.post_process(post_process.DoesNotRun, 'isolate tests (with patch)'),
      api.post_process(post_process.MustRun, 'prepare skylab tests'),
      api.post_process(post_process.DoesNotRun, 'swarming trigger properties'),
      api.post_process(post_process.MustRun, 'skylab trigger properties'),
      api.post_process(post_process.MustRun,
                       'check_static_initializers (with patch)'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'override deps output',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-compilator',
          revision='deadbeef',
      ),
      api.platform.name('linux'),
      api.path.exists(
          api.path.checkout_dir.joinpath('out', '666d-fake-compilator',
                                         'browser_tests')),
      ctbc_properties(),
      api.properties(
          InputProperties(
              orchestrator=InputProperties.Orchestrator(
                  builder_name='fake-orchestrator',
                  builder_group='fake-try-group'))),
      api.step_data(
          'bot_update',
          api.json.output({
              'did_run': True,
              'fixed_revisions': {
                  'src': '3cf43af95eeb84f4f09861e74c6f3709e9a8e12a',
                  'src/v8': 'deadbeef',
              },
              'manifest': {
                  'src': {
                      'repository':
                          ('https://chromium.googlesource.com/chromium/src.git'
                          ),
                      'revision': '3cf43af95eeb84f4f09861e74c6f3709e9a8e12a',
                  },
                  'src/v8': {
                      'repository':
                          'https://chromium.googlesource.com/v8/v8.git',
                      'revision':
                          'deadbeef',
                  },
              },
              'patch_root': 'src/v8',
              'properties': {
                  'got_revision': '3cf43af95eeb84f4f09861e74c6f3709e9a8e12a',
                  'got_revision_cp': 'refs/heads/main@{#1183721}',
                  'got_v8_revision': "deadbeef",
                  'got_v8_revision_cp': 'refs/heads/main@{#89522}',
              },
              'root': 'src',
          }),
      ),
      override_test_spec(),
      api.post_process(post_process.PropertiesContain, 'override_deps'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'archive_src_side_runtime_deps',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-compilator',
          revision='deadbeef',
      ),
      api.platform.name('linux'),
      api.path.exists(
          api.path.checkout_dir.joinpath('out', '666d-fake-compilator',
                                         'browser_tests')),
      ctbc_properties(),
      api.properties(
          InputProperties(
              orchestrator=InputProperties.Orchestrator(
                  builder_name='fake-orchestrator',
                  builder_group='fake-try-group'))),
      override_test_spec(),
      api.step_data(
          'archive src-side dep paths.read orchestrator_all.runtime_deps',
          api.file.read_text(
              '../../testing/buildbot/chromium.linux.json\n'
              '../../testing/merge_scripts/merge_api.py\n'
              '../../testing/merge_scripts/standard_gtest_merge.py')),
      api.tryserver.get_files_affected_by_patch(
          ['foo.cc', 'bar/baz.cc', 'testing/buildbot/chromium.linux.json']),
      api.path.exists(
          api.path.checkout_dir / 'foo.cc',
          api.path.checkout_dir / 'testing/buildbot/chromium.linux.json',
      ),
      api.post_process(
          post_process.LogContains, 'archive src-side dep paths', 'dep paths', [
              '[CACHE]/builder/src/testing/merge_scripts/merge_api.py',
              '[CACHE]/builder/src/testing/merge_scripts/'
              'standard_gtest_merge.py',
              '[CACHE]/builder/src/foo.cc',
              'testing/buildbot/chromium.linux.json',
          ]),
      api.post_process(
          post_process.LogDoesNotContain,
          'archive src-side dep paths',
          'dep paths',
          [
              '[CACHE]/builder/src/bar/baz.cc',
              ('"[CACHE]/builder/src/testing/buildbot/chromium.linux.json",'
               ' \n  '
               '"[CACHE]/builder/src/testing/buildbot/chromium.linux.json"'),
          ],
      ),
      api.post_process(
          post_process.StepCommandContains,
          'compile (with patch)',
          ['infra/orchestrator:orchestrator_all'],
      ),
      api.post_process(post_process.MustRun,
                       'archive src-side dep paths.archive src-side deps'),
      api.post_process(post_process.PropertiesContain, 'src_side_deps_digest'),
      api.post_process(post_process.DropExpectation),
  )

  def make_git_diff_affected_files(count):
    file_dir = '[CACHE]/builder/src/'
    output = ''
    for i in range(count):
      output += file_dir + 'foo{}.cc'.format(i) + '\n'
    return output

  yield api.test(
      'archive_src_side_runtime_deps_skipping_coverage',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-compilator',
          revision='deadbeef',
      ),
      api.platform.name('linux'),
      api.path.exists(
          api.path.checkout_dir.joinpath('out', '666d-fake-compilator',
                                         'browser_tests')),
      api.code_coverage(use_clang_coverage=True),
      ctbc_properties(),
      api.properties(
          InputProperties(
              orchestrator=InputProperties.Orchestrator(
                  builder_name='fake-orchestrator',
                  builder_group='fake-try-group'))),
      override_test_spec(),
      api.step_data(
          'git diff to analyze patch',
          api.raw_io.stream_output(
              make_git_diff_affected_files(MAX_CANDIDATE_FILES + 1)),
      ),
      api.step_data(
          'archive src-side dep paths.read orchestrator_all.runtime_deps',
          api.file.read_text(
              '../../testing/merge_scripts/merge_api.py\n'
              '../../testing/merge_scripts/standard_gtest_merge.py')),
      api.post_process(
          post_process.LogDoesNotContain, 'archive src-side dep paths',
          'dep paths',
          make_git_diff_affected_files(MAX_CANDIDATE_FILES + 1).split()),
      api.post_process(
          post_process.LogContains, 'archive src-side dep paths', 'dep paths', [
              '[CACHE]/builder/src/testing/merge_scripts/merge_api.py',
              '[CACHE]/builder/src/testing/merge_scripts/'
              'standard_gtest_merge.py',
          ]),
      api.post_process(
          post_process.StepCommandContains,
          'compile (with patch)',
          ['infra/orchestrator:orchestrator_all'],
      ),
      api.post_process(
          post_process.LogDoesNotContain,
          'isolate tests (with patch)',
          'json.output',
          [chromium_tests.ALL_TEST_BINARIES_ISOLATE_NAME],
      ),
      api.post_process(post_process.PropertyEquals, 'skipping_coverage', True),
      api.post_process(post_process.MustRun,
                       'archive src-side dep paths.archive src-side deps'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'archive_affected_files_windows',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-compilator',
          revision='deadbeef'),
      api.platform.name('win'),
      api.path.exists(
          api.path.checkout_dir.joinpath('out', '666d-fake-compilator',
                                         'browser_tests')),
      api.code_coverage(use_clang_coverage=True),
      ctbc_properties(),
      api.properties(
          InputProperties(
              orchestrator=InputProperties.Orchestrator(
                  builder_name='fake-orchestrator',
                  builder_group='fake-try-group'))),
      override_test_spec(),
      api.tryserver.get_files_affected_by_patch(['foo.cc', 'bar/baz.cc']),
      api.path.exists(api.path.checkout_dir / 'foo.cc'),
      api.step_data(
          'archive src-side dep paths.read orchestrator_all.runtime_deps',
          api.file.read_text(
              '..\\..\\testing\\merge_scripts\\merge_api.py\n'
              '..\\..\\testing\\merge_scripts\\standard_gtest_merge.py')),
      api.post_process(post_process.LogDoesNotContain,
                       'archive src-side dep paths', 'dep paths',
                       ['[CACHE]\\\\builder\\\\src\\\\bar\\\\baz.cc']),
      api.post_process(
          post_process.LogContains, 'archive src-side dep paths', 'dep paths', [
              'merge_api.py',
              '[CACHE]\\\\builder\\\\src\\\\testing\\\\merge_scripts',
              'standard_gtest_merge.py', '[CACHE]\\\\builder\\\\src\\\\foo.cc'
          ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'archive_src_side_runtime_deps_glob_pattern',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-compilator',
          revision='deadbeef',
      ),
      api.platform.name('linux'),
      api.path.exists(
          api.path.checkout_dir.joinpath('out', '666d-fake-compilator',
                                         'browser_tests')),
      ctbc_properties(),
      api.properties(
          InputProperties(
              orchestrator=InputProperties.Orchestrator(
                  builder_name='fake-orchestrator',
                  builder_group='fake-try-group'))),
      override_test_spec(),
      api.step_data(
          'archive src-side dep paths.read orchestrator_all.runtime_deps',
          api.file.read_text('../../testing/buildbot/*.json')),
      api.step_data(
          'archive src-side dep paths.get files that match pattern',
          api.file.glob_paths([
              'testing/buildbot/chromium.linux.json',
              'testing/buildbot/chromium.android.json'
          ])),
      api.post_process(
          post_process.LogContains, 'archive src-side dep paths', 'dep paths', [
              '[CACHE]/builder/src/testing/buildbot/chromium.linux.json',
              '[CACHE]/builder/src/testing/buildbot/chromium.android.json'
          ]),
      api.post_process(
          post_process.StepCommandContains,
          'compile (with patch)',
          ['infra/orchestrator:orchestrator_all'],
      ),
      api.post_process(post_process.MustRun,
                       'archive src-side dep paths.archive src-side deps'),
      api.post_process(post_process.PropertiesContain, 'src_side_deps_digest'),
      api.post_process(post_process.DropExpectation),
  )


  yield api.test(
      'full run rts',
      api.properties(
          **{
              "$recipe_engine/cv": {
                  "active": True,
                  "dryRun": True,
                  "runMode": "FULL_RUN",
                  "topLevel": True
              }
          }),
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-compilator',
          revision='deadbeef',
      ),
      api.chromium_tests_builder_config.databases(_TEST_BUILDERS,
                                                  _TEST_TRYBOTS),
      api.properties(
          InputProperties(
              orchestrator=InputProperties.Orchestrator(
                  builder_group='tryserver.chromium.test',
                  builder_name='rts-rel'),
              rts_model='smart-test-selection')),
      api.chromium_tests.read_targets_spec('chromium.test', {
          'chromium-rel': {
              'gtest_tests': [{
                  'test': 'base_unittests',
              }],
          },
      }),
      api.post_process(
          post_process.StepCommandContains,
          'generate_build_files (with patch)',
          ['smart-test-selection'],
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'full run rts without_patch',
      api.properties(
          **{
              "$recipe_engine/cv": {
                  "active": True,
                  "dryRun": True,
                  "runMode": "FULL_RUN",
                  "topLevel": True
              }
          }),
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-compilator',
          revision='deadbeef',
      ),
      api.chromium_tests_builder_config.databases(_TEST_BUILDERS,
                                                  _TEST_TRYBOTS),
      api.properties(
          InputProperties(
              orchestrator=InputProperties.Orchestrator(
                  builder_group='tryserver.chromium.test',
                  builder_name='rts-rel'),
              test_targets=['base_unittests']),
          rts_model='smart-test-selection'),
      api.chromium_tests.read_targets_spec('chromium.test', {
          'chromium-rel': {
              'gtest_tests': [{
                  'test': 'base_unittests',
              }],
          },
      }),
      api.post_process(
          post_process.StepCommandContains,
          'generate_build_files (without patch)',
          ['smart-test-selection'],
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'global_shutdown',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-compilator',
          revision='deadbeef',
      ),
      api.properties(swarming_parent_run_id='some-parent-run-id'),
      api.platform.name('linux'),
      api.path.exists(
          api.path.checkout_dir.joinpath('out', '666d-fake-compilator',
                                         'browser_tests')),
      ctbc_properties(),
      api.properties(
          InputProperties(
              orchestrator=InputProperties.Orchestrator(
                  builder_name='fake-orchestrator',
                  builder_group='fake-try-group'))),
      override_test_spec(),
      api.runtime.global_shutdown_on_step('isolate tests (with patch)'),
      api.post_process(post_process.SummaryMarkdownRE,
                       '.*causing this build to be canceled.*'),
      api.expect_status('CANCELED'),
      api.post_process(post_process.DropExpectation),
  )

  def without_patch_assert_workplan(assert_, workplan: WorkPlan):
    check_ids = set(c.identifier.id for c in workplan.checks)
    if not assert_(
        check_ids == {
            chromium_tests.WITHOUT_PATCH_SOURCE_CHECK_ID,
            chromium_tests.WITHOUT_PATCH_BUILD_CHECK_ID,
        }):
      return  # pragma: no cover

    # without patch source check verifications #################################
    # The source check is created by bot_update, just verify it's of appropriate
    # kind and is final, none of the other details since the check isn't being
    # read by the recipe code
    source_check = turboci.get_check_by_short_id(
        workplan, chromium_tests.WITHOUT_PATCH_SOURCE_CHECK_ID)
    assert_(source_check.kind == CheckKind.CHECK_KIND_SOURCE)
    assert_(source_check.state == CheckState.CHECK_STATE_FINAL)

    # without patch build check verifications ##################################
    build_check = turboci.get_check_by_short_id(
        workplan, chromium_tests.WITHOUT_PATCH_BUILD_CHECK_ID)
    assert_(build_check.kind == CheckKind.CHECK_KIND_BUILD)
    assert_(build_check.state == CheckState.CHECK_STATE_FINAL)

    # Verify that the without patch build check depends on the without patch
    # source check
    build_check_deps = {
        edge.check.identifier.id for edge in build_check.dependencies.edges
    }
    assert_(build_check_deps == {chromium_tests.WITHOUT_PATCH_SOURCE_CHECK_ID})

    # Verify the options on the without patch build check
    build_check_options = turboci.get_option(BuildCheckOptions, build_check)
    expected_build_check_options = BuildCheckOptions(
        target=BuildCheckOptions.BuildTarget(
            name='chromium/try/fake-orchestrator',
            product=Product.PRODUCT_BROWSER,
        ))
    assert_(build_check_options == expected_build_check_options)

    compile_targets_options = turboci.get_option(CompileTargetsOptions,
                                                 build_check)
    expected_compile_targets_options = CompileTargetsOptions(compile_targets=[
        'browser_tests', 'infra/orchestrator:orchestrator_all'
    ])
    assert_(compile_targets_options == expected_compile_targets_options)

    # Verify the results on the without patch build check
    build_check_results = turboci.get_results(BuildCheckResult, build_check)
    expected_build_check_results = [BuildCheckResult(success=True)]
    assert_(build_check_results == expected_build_check_results)

  yield api.test(
      'without_patch',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-compilator',
          revision='deadbeef',
      ),
      api.platform.name('linux'),
      api.code_coverage(use_clang_coverage=True),
      api.path.exists(
          api.path.checkout_dir.joinpath('out', '666d-fake-compilator',
                                         'browser_tests')),
      ctbc_properties(),
      api.properties(
          InputProperties(
              orchestrator=InputProperties.Orchestrator(
                  builder_name='fake-orchestrator',
                  builder_group='fake-try-group'),
              test_targets=['browser_tests'])),
      override_test_spec(),
      api.post_process(post_process.StepTextContains, 'report builders', [
          "running tester 'fake-tester' on group 'fake-group' against "
          "builder 'fake-builder' on group 'fake-group'"
      ]),
      api.post_process(post_process.StepCommandDoesNotContain,
                       'bot_update (without patch)', ['--patch_ref']),
      api.post_process(post_process.MustRun, 'compile (without patch)'),
      api.post_process(post_process.MustRun, 'isolate tests (without patch)'),
      api.post_process(post_process.MustRun, 'swarming trigger properties'),
      api.post_process(post_process.DoesNotRun, 'compile (with patch)'),
      api.post_process(post_process.DoesNotRun, 'isolate tests (with patch)'),
      api.post_process(post_process.DoesNotRun,
                       'check_static_initializers (with patch)'),
      api.post_process(post_process.DropExpectation),
      api.assert_workplan(without_patch_assert_workplan),
  )

  yield api.test(
      'without_patch_skylab_tests',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-compilator',
          revision='deadbeef',
      ),
      api.platform.name('linux'),
      api.path.exists(
          api.path.cache_dir.joinpath(
              'builder/src/out/666d-fake-compilator/lacros_all_tast_tests.isolate'
          ),
          api.path.start_dir.joinpath('squashfs', 'squashfs-tools',
                                      'mksquashfs'),
      ),
      ctbc_properties(
          builder_spec=ctbc.BuilderSpec.create(
              gclient_config='chromium',
              chromium_config='chromium',
              skylab_gs_bucket='chrome-test-builds',
          ),),
      api.properties(
          InputProperties(
              orchestrator=InputProperties.Orchestrator(
                  builder_name='fake-orchestrator',
                  builder_group='fake-try-group'),
              test_targets=['browser_tests', 'lacros_all_tast_tests'])),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'scripts': [{
                      "isolate_profile_data": True,
                      "name": "check_static_initializers",
                      "script": "check_static_initializers.py",
                      "test_id_prefix": "ninja://check_static_initializers/"
                  }],
              },
              'fake-tester': {
                  'gtest_tests': [{
                      'name': 'browser_tests',
                      'swarming': {},
                  }],
                  'skylab_tests': [{
                      'name': 'lacros_all_tast_tests',
                      'cros_board': 'volteer',
                      'autotest_name': 'chromium',
                  }],
              },
          }),
      api.step_data(
          'prepare skylab tests.collect runtime deps for '
          'lacros_all_tast_tests.read isolate file',
          api.file.read_text(SKYLAB_ISOLATE_TEXT)),
      api.post_process(post_process.StepTextContains, 'report builders', [
          "running tester 'fake-tester' on group 'fake-group' against "
          "builder 'fake-builder' on group 'fake-group'"
      ]),
      api.post_process(post_process.StepCommandDoesNotContain,
                       'bot_update (without patch)', ['--patch_ref']),
      api.post_process(post_process.MustRun, 'compile (without patch)'),
      api.post_process(post_process.MustRun, 'isolate tests (without patch)'),
      api.post_process(post_process.MustRun, 'prepare skylab tests'),
      api.post_process(post_process.MustRun, 'swarming trigger properties'),
      api.post_process(post_process.MustRun, 'skylab trigger properties'),
      api.post_process(post_process.DoesNotRun, 'compile (with patch)'),
      api.post_process(post_process.DoesNotRun, 'isolate tests (with patch)'),
      api.post_process(post_process.DoesNotRun,
                       'check_static_initializers (with patch)'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'no_compile_no_isolate',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-compilator',
          revision='deadbeef',
      ),
      ctbc_properties(),
      api.properties(
          InputProperties(
              orchestrator=InputProperties.Orchestrator(
                  builder_name='fake-orchestrator',
                  builder_group='fake-try-group'))),
      override_test_spec(),
      api.filter.no_dependency(),
      api.post_process(post_process.StepTextContains, 'report builders', [
          'tester \'fake-tester\' on group \'fake-group\'',
          'builder \'fake-builder\' on group \'fake-group\''
      ]),
      api.post_process(post_process.DoesNotRun, 'compile (with patch)'),
      api.post_process(post_process.DoesNotRun, 'isolate tests (with patch)'),
      api.post_process(post_process.DoesNotRun, 'swarming trigger properties'),
      api.post_process(post_process.DoesNotRun, 'archive src-side dep paths'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'compile_failed',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-compilator',
          revision='deadbeef',
      ),
      ctbc_properties(),
      api.properties(
          InputProperties(
              orchestrator=InputProperties.Orchestrator(
                  builder_name='fake-orchestrator',
                  builder_group='fake-try-group'))),
      override_test_spec(),
      api.override_step_data('compile (with patch)', retcode=1),
      api.post_process(post_process.StepTextContains, 'report builders', [
          'tester \'fake-tester\' on group \'fake-group\'',
          'builder \'fake-builder\' on group \'fake-group\''
      ]),
      api.post_process(post_process.DoesNotRun, 'isolate tests (with patch)'),
      api.post_process(post_process.DoesNotRun, 'swarming trigger properties'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'failing_local_test',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-compilator',
          revision='deadbeef',
      ),
      ctbc_properties(),
      api.properties(
          InputProperties(
              orchestrator=InputProperties.Orchestrator(
                  builder_name='fake-orchestrator',
                  builder_group='fake-try-group'))),
      override_test_spec(),
      api.override_step_data('check_static_initializers (with patch)',
                             api.test_utils.canned_gtest_output(False)),
      api.post_process(post_process.StepTextContains, 'report builders', [
          'tester \'fake-tester\' on group \'fake-group\'',
          'builder \'fake-builder\' on group \'fake-group\''
      ]),
      api.post_process(post_process.MustRun, 'swarming trigger properties'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  def _generate_test_result(test_id,
                            test_variant,
                            status=test_result_pb2.PASS,
                            tags=None):
    vh = 'variant_hash'
    tr = test_result_pb2.TestResult(
        test_id=test_id,
        variant=test_variant,
        variant_hash=vh,
        expected=False,
        status=status,
    )
    if tags:
      all_tags = getattr(tr, 'tags')
      all_tags.append(tags)
    return tr

  correct_variant = resultdb_common.Variant()
  variant_def = getattr(correct_variant, 'def')
  variant_def['os'] = 'Ubuntu-18'
  variant_def['test_suite'] = ('check_static_initializers')

  tags = resultdb_common.StringPair(key='test_name', value='Test:Test1')

  test_id = ('ninja://check_static_initializers/Test:Test1')
  inv = 'invocations/build:8945511751514863184'
  current_patchset_invocations = {
      inv:
          api.resultdb.Invocation(test_results=[
              _generate_test_result(test_id, correct_variant, tags=tags)
          ])
  }

  recent_run = test_history.QueryTestHistoryResponse(
      verdicts=[], next_page_token='dummy_token')

  yield api.test(
      'basic_flakiness',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-compilator',
          revision='deadbeef',
      ),
      api.platform.name('linux'),
      api.path.exists(
          api.path.checkout_dir.joinpath('out', '666d-fake-compilator',
                                         'browser_tests')),
      ctbc_properties(),
      api.properties(
          InputProperties(
              orchestrator=InputProperties.Orchestrator(
                  builder_name='fake-orchestrator',
                  builder_group='fake-try-group'))),
      override_test_spec(),
      # This additional analyze step is run by the flakiness module to ensure
      # that there's a test file change associated with the patch.
      api.step_data(
          'git diff to analyze patch',
          api.raw_io.stream_output('chrome/test.cc\ncomponents/file2.cc')),
      api.resultdb.query(
          current_patchset_invocations,
          ('check_static_initializers results'),
      ),
      api.flakiness(check_for_flakiness=True,),
      api.luci_analysis.query_test_history(
          recent_run,
          'ninja://check_static_initializers/Test:Test1',
          parent_step_name='searching_for_new_tests',
      ),
      api.resultdb.query(
          inv_bundle=current_patchset_invocations,
          step_name=('test new tests for flakiness.'
                     'check_static_initializers results'),
      ),
      api.post_process(post_process.MustRun, 'searching_for_new_tests'),
      api.post_process(post_process.MustRun, 'test new tests for flakiness'),
      api.post_process(post_process.MustRun, 'calculate flake rates'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'flaky_test_failure',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-compilator',
          revision='deadbeef',
      ),
      api.platform.name('linux'),
      api.path.exists(
          api.path.checkout_dir.joinpath('out', '666d-fake-compilator',
                                         'browser_tests')),
      ctbc_properties(),
      api.properties(
          InputProperties(
              orchestrator=InputProperties.Orchestrator(
                  builder_name='fake-orchestrator',
                  builder_group='fake-try-group'))),
      override_test_spec(),
      # This additional analyze step is run by the flakiness module to ensure
      # that there's a test file change associated with the patch.
      api.step_data(
          'git diff to analyze patch',
          api.raw_io.stream_output('chrome/test.cc\ncomponents/file2.cc')),
      api.resultdb.query(
          current_patchset_invocations,
          ('check_static_initializers results'),
      ),
      api.flakiness(check_for_flakiness=True,),
      api.luci_analysis.query_test_history(
          recent_run,
          'ninja://check_static_initializers/Test:Test1',
          parent_step_name='searching_for_new_tests',
      ),
      api.override_step_data(
          'test new tests for flakiness.check_static_initializers results',
          stdout=api.json.invalid(
              api.test_utils.rdb_results(
                  'check_static_initializers',
                  flaky_failing_tests=['Test.One'],
              ))),
      api.post_process(post_process.SummaryMarkdownRE,
                       '.*check_static_initializers.*'),
      api.post_process(post_process.MustRun, 'calculate flake rates'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'dont_compile_skip_tests',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-compilator',
          revision='deadbeef',
          tags=api.buildbucket.tags(
              cq_equivalent_cl_group_key='12345', cq_attempt_key='67890'),
      ),
      api.platform.name('linux'),
      api.path.exists(
          api.path.checkout_dir.joinpath('out', '666d-fake-compilator',
                                         'browser_tests')),
      ctbc_properties(),
      api.properties(
          InputProperties(
              orchestrator=InputProperties.Orchestrator(
                  builder_name='fake-orchestrator',
                  builder_group='fake-try-group'))),
      override_test_spec(),
      api.cv(run_mode='FULL_RUN'),
      api.chromium_tests.simulate_previous_build(
          test_statuses={'browser_tests': 'Success'}),
      api.post_process(post_process.StepTextContains, 'report builders', [
          "running tester 'fake-tester' on group 'fake-group' against "
          "builder 'fake-builder' on group 'fake-group'"
      ]),
      api.post_process(post_process.MustRun, 'compile (with patch)'),
      api.post_process(
          post_process.StepCommandDoesNotContain,
          'compile (with patch)',
          ['browser_tests'],
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'skip_analysis_reasons',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-compilator',
          revision='deadbeef',
          tags=api.buildbucket.tags(
              cq_equivalent_cl_group_key='12345', cq_attempt_key='67890'),
      ),
      api.platform.name('linux'),
      api.path.exists(
          api.path.checkout_dir.joinpath('out', '666d-fake-compilator',
                                         'browser_tests')),
      ctbc_properties(),
      api.properties(
          InputProperties(
              orchestrator=InputProperties.Orchestrator(
                  builder_name='fake-orchestrator',
                  builder_group='fake-try-group'),
              skip_analysis_reasons=[
                  'fake-reason1',
                  'fake-reason2',
              ])),
      override_test_spec(),
      api.cv(run_mode='FULL_RUN'),
      api.post_check(post_process.StepTextContains, 'analyze',
                     ['skipping analyze', 'fake-reason1', 'fake-reason2']),
      api.post_process(post_process.DropExpectation),
  )
