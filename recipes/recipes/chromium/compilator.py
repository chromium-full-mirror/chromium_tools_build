# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Compiles with patch and isolates tests"""

from recipe_engine import post_process
from recipe_engine import turboci
from recipe_engine.config_types import Path

from RECIPE_MODULES.build import chromium_types
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
from PB.recipe_engine import result as result_pb2
from PB.recipes.build.chromium.compilator import InputProperties
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
from PB.turboci.graph.orchestrator.v1.graph_view import GraphView


DEPS = [
    'chromium',
    'chromium_checkout',
    'chromium_rts',
    'chromium_swarming',
    'chromium_tests',
    'chromium_tests_builder_config',
    'code_coverage',
    'depot_tools/tryserver',
    'filter',
    'flakiness',
    'isolate',
    'recipe_engine/buildbucket',
    'recipe_engine/cas',
    'recipe_engine/cv',
    'recipe_engine/context',
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

ORCHESTRATOR_ALL_TARGET_NAME = 'infra/orchestrator:orchestrator_all'
ORCHESTRATOR_RUNTIME_DEPS_FILE = 'orchestrator_all.runtime_deps'


def compilator_steps(api, properties):
  api.tryserver.require_is_tryserver()

  with api.chromium.chromium_layout():
    orchestrator = properties.orchestrator.builder_name
    builder_group = properties.orchestrator.builder_group
    if not orchestrator or not builder_group:
      raise api.step.InfraFailure(
          'this builder is intended to be triggered by its corresponding'
          ' orchestrator builder, it is an error to trigger it directly')
    orch_builder_id = chromium_types.BuilderId.create_for_group(
        builder_group, orchestrator)

    _, orch_builder_config = (
        api.chromium_tests_builder_config.lookup_builder(
            builder_id=orch_builder_id))

    api.chromium_tests.report_builders(orch_builder_config)

    # This should eventually be replaced with a property that controls compile
    # with RTS independent of compilator
    api.chromium_rts.rts_model = properties.rts_model

    api.chromium_tests.configure_build(orch_builder_config)
    api.chromium.apply_config('trybot_flavor')

    # Assumes that the compilator is in the same project and bucket as the
    # orchestrator
    bb_id = api.buildbucket.build.builder
    builder_full_name = (
        f'{bb_id.project}/{bb_id.bucket}/{orch_builder_id.builder}')

    # test_targets implies that this compilator build must be compiled
    # without a patch so that the orchestrator can retry these tests
    # without patch
    if properties.test_targets:
      update_result, build_dir, targets_config = api.chromium_tests.prepare_checkout(
          orch_builder_config,
          timeout=3600,
          no_fetch_tags=True,
          enforce_fetch=True,
          patch=False,
          runhooks_suffix='without patch',
          turboci_source_check_id=chromium_tests.WITHOUT_PATCH_SOURCE_CHECK_ID)

      # code coverage is ignored for without patch steps, but compile will
      # error if there is no files_to_instrument.txt file
      if api.code_coverage.using_coverage:
        api.code_coverage.instrument([])

      # properties.test_targets should only be targets required for
      # isolated swarming tests or skylab tests
      test_suites = [
          t for t in targets_config.all_tests
          if t.target_name in properties.test_targets and
          (t.runs_on_swarming or t.runs_on_skylab)
      ]

      api.chromium_tests.turboci.create_build_check(
          chromium_tests.WITHOUT_PATCH_BUILD_CHECK_ID,
          chromium_tests.WITHOUT_PATCH_SOURCE_CHECK_ID,
          builder_full_name=builder_full_name)

      raw_result, execution_info = (
          api.chromium_tests.build_and_isolate_failing_tests(
              build_dir,
              orch_builder_id,
              orch_builder_config,
              test_suites,
              update_result,
              'without patch',
              additional_compile_targets=[ORCHESTRATOR_ALL_TARGET_NAME],
              turboci_build_check_id=chromium_tests.WITHOUT_PATCH_BUILD_CHECK_ID
          ))
    else:
      update_result, build_dir, targets_config = (
          api.chromium_tests.prepare_checkout(
              orch_builder_config,
              timeout=3600,
              no_fetch_tags=True,
              turboci_source_check_id=chromium_tests.SOURCE_CHECK_ID))

      turboci_analyze_check_id = api.chromium_tests.turboci.create_build_check(
          chromium_tests.BUILD_CHECK_ID,
          chromium_tests.SOURCE_CHECK_ID,
          create_analyze_check=True,
          builder_full_name=builder_full_name)

      raw_result, task = api.chromium_tests.build_affected_targets(
          orch_builder_id,
          orch_builder_config,
          update_result,
          build_dir,
          targets_config,
          isolate_output_files_for_coverage=True,
          additional_compile_targets=[ORCHESTRATOR_ALL_TARGET_NAME],
          skip_analysis_reasons=properties.skip_analysis_reasons,
          turboci_build_check_id=chromium_tests.BUILD_CHECK_ID,
          turboci_analyze_check_id=turboci_analyze_check_id,
      )
      execution_info = task.swarming_execution_info
      test_suites = task.test_suites
      update_result = task.update_result
      build_dir = task.build_dir

      # In case a without patch build is needed later, output the needed
      # deps override
      if update_result.patch_root != update_result.source_root:
        patch_root = update_result.patch_root.name
        deps_overrides = {}
        deps_revision_dict = update_result.manifest[patch_root]
        deps_overrides[patch_root] = deps_revision_dict['revision']
        output_deps_override = api.step.empty('output override_deps')
        output_deps_override.presentation.properties['override_deps'] = (
            deps_overrides)

    if raw_result and raw_result.status != common_pb.SUCCESS:
      return raw_result

    checkout_dir = update_result.checkout_dir
    source_dir = update_result.source_root.path
    if any((t.runs_on_swarming or t.runs_on_skylab) and t.is_enabled
           for t in test_suites):
      affected_files_to_archive = []
      # If properties.test_targets exist, it means this build is doing a
      # "without patch" so there's no affected files to archive
      if (not properties.test_targets and
          not api.code_coverage.skipping_coverage):
        deleted_files = get_deleted_files(api, source_dir, task.affected_files)
        affected_files_to_archive = [
            # In case this is a windows compilator
            str(source_dir / f).replace('/', api.path.sep)
            for f in task.affected_files
            # If the affected file is deleted, don't attempt to archive it or
            # else you'll get a file not found error
            if f not in deleted_files
        ]
      archive_src_side_deps(
          api,
          orch_builder_config,
          checkout_dir,
          source_dir,
          build_dir,
          affected_files_to_archive,
      )

      if any(t.runs_on_swarming and t.is_enabled for t in test_suites):
        # Isolate the tests first so the Orchestrator can trigger them asap
        trigger_properties = execution_info.ensure_command_lines_archived(
            api.chromium_tests).as_trigger_prop()

        properties_step = api.step('swarming trigger properties', [])
        properties_step.presentation.properties[
            'swarming_trigger_properties'] = trigger_properties
        properties_step.presentation.logs[
            'swarming_trigger_properties'] = api.m.json.dumps(
                trigger_properties, indent=2)

      if any(t.runs_on_skylab and t.is_enabled for t in test_suites):
        skylab_tests = [t for t in test_suites if t.runs_on_skylab]
        skylab_trigger_properties = (
            api.chromium_tests._get_skylab_trigger_properties(skylab_tests))
        properties_step = api.step('skylab trigger properties', [])
        properties_step.presentation.properties[
            'skylab_trigger_properties'] = skylab_trigger_properties
        properties_step.presentation.logs[
            'skylab_trigger_properties'] = api.m.json.dumps(
                skylab_trigger_properties, indent=2)

      post_step = api.step('post output trigger properties', [])
      post_step.presentation.tags['chromium.outputted_trigger_properties'] = (
          'outputted swarming and/or skylab trigger properties')

    local_tests = [t for t in test_suites if t.runs_locally]
    if local_tests:
      test_runner = api.chromium_tests.create_test_runner(
          checkout_dir,
          source_dir,
          build_dir,
          local_tests,
          suffix='with patch',
      )
      with api.chromium_tests.wrap_chromium_tests(
          checkout_dir, source_dir, build_dir, tests=local_tests):
        raw_result = test_runner()
        if raw_result and raw_result.status != common_pb.SUCCESS:
          return raw_result

      # check for new flaky tests on successful run w/ patch
      if api.flakiness.check_for_flakiness:
        new_tests = api.flakiness.find_tests_for_flakiness(
            local_tests, affected_files=task.affected_files)
        if new_tests:
          return api.chromium_tests.run_tests_for_flakiness(
              checkout_dir, source_dir, build_dir, new_tests)

    return raw_result


def archive_src_side_deps(
    api,
    orch_builder_config: ctbc.BuilderConfig,
    checkout_dir: Path,
    source_dir: Path,
    build_dir: Path,
    affected_files,
):
  """Archives src-side deps that the Orchestrator needs to run tests/coverage.

  Affected files is also needed by the orchestrator to run code coverage

  Args:
    checkout_dir: The directory where the checkout was performed.
    source_dir: The path to the top-level repo.
    affected_files (list): List of absolute string paths
  """
  with api.step.nest('archive src-side dep paths') as nested_step:
    dep_paths = set(get_src_side_dep_paths(api, source_dir, build_dir))
    # Affected files are needed in when coverage is collected
    dep_paths.update(affected_files)
    # Include the targets spec files so that the orchestrator can create the
    # test objects for running remote tests
    targets_spec_dir = api.chromium_tests.get_targets_spec_dir(
        source_dir, orch_builder_config)
    dep_paths.update(
        str(targets_spec_dir / f)
        for f in orch_builder_config.targets_spec_files.values())
    dep_paths = sorted(dep_paths)

    # We need the files relative to the checkout dir so they can get downloaded
    # correctly on the orchestrator. And the .isolate file inherits the cwd of
    # the file itself, so create the file using a tmp name that should be
    # sufficiently unique to this build.
    isolate_file = api.path.join(
        source_dir, '%s_archive_deps.isolate' % api.swarming.task_id)
    rel_dep_paths = []
    for p in dep_paths:
      rel_dep_paths.append(api.path.relpath(p, source_dir))
    api.isolate.write_isolate_file(isolate_file, rel_dep_paths)
    digest = api.isolate.isolate('archive src-side deps', isolate_file)
    api.file.remove('rm %s' % isolate_file, isolate_file)

    targets_spec_dir = api.chromium_tests.get_targets_spec_dir(
        source_dir, orch_builder_config)
    relative_test_spec_dir = api.path.relpath(targets_spec_dir, source_dir)
    # On windows compilators, this would use a `\\` path separator instead of
    # a `/` that the linux orchestrators need to construct Paths
    relative_test_spec_dir = relative_test_spec_dir.replace(api.path.sep, '/')

    nested_step.properties['src_side_test_spec_dir'] = (relative_test_spec_dir)
    nested_step.properties['src_side_deps_digest'] = digest
    nested_step.logs['dep paths'] = api.json.dumps(dep_paths, indent=2)


def get_src_side_dep_paths(api, source_dir: Path, build_dir: Path):
  """Get src-side paths to archive.

  The chromium compile step writes which src-side deps to archive.
  The orchestrator build will use the CAS hash to download these deps to run
  tests and code coverage.

  Returns:
    List of string paths
  """
  dep_paths = set()
  runtime_deps_file = build_dir / ORCHESTRATOR_RUNTIME_DEPS_FILE
  paths = (
      api.file.read_text('read orchestrator_all.runtime_deps',
                         runtime_deps_file).rstrip().split('\n'))
  for path in paths:
    # Paths written in these files look like '../../testing/X.py' relative
    # to the output dir
    file_path = api.path.relpath(build_dir / path, source_dir)
    file_path = source_dir / file_path

    # Path can be a regex pattern
    if "*" in str(file_path):
      paths = api.file.glob_paths('get files that match pattern', source_dir,
                                  str(file_path))
      dep_paths.update([str(p) for p in paths])
    else:
      dep_paths.add(str(file_path))
  return list(dep_paths)


def get_deleted_files(api, source_dir, affected_files):
  deleted_files = []
  for f in affected_files:
    path = source_dir / f
    # In case this is a windows compilator
    path = str(path).replace('/', api.path.sep)

    if not api.path.exists(path):
      deleted_files.append(f)
  return deleted_files


def create_orchestrator_milo_link(swarming_task_id, host):
  return 'https://luci-milo.appspot.com/swarming/task/{}?server={}'.format(
      swarming_task_id, host)


def global_shutdown_summary_markdown(parent_build_url):
  message = ('Parent orchestrator [build]({}) ended, causing this build to be '
             'canceled.')
  return message.format(parent_build_url)


def RunSteps(api, properties):
  try:
    return compilator_steps(api, properties)
  finally:
    if api.runtime.in_global_shutdown:
      # pylint: disable=lost-exception
      # Compilator builds can experience a variety of exceptions when its
      # swarming tasks are killed, depending on where in the recipe it was
      # killed.
      # The important part is that the build status is CANCELED.
      return result_pb2.RawResult(
          status=common_pb.CANCELED,
          summary_markdown=global_shutdown_summary_markdown(
              create_orchestrator_milo_link(
                  api.buildbucket.swarming_parent_run_id,
                  api.buildbucket.backend_hostname)))


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

  def basic_assert_graph(assert_, graph: GraphView):
    analyze_check_id = f'{chromium_tests.BUILD_CHECK_ID} analyze'
    check_ids = set(c.check.identifier.id for c in graph.checks.values())
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
    source_check = turboci.get_check_view(graph,
                                          chromium_tests.SOURCE_CHECK_ID).check
    assert_(source_check.kind == CheckKind.CHECK_KIND_SOURCE)
    assert_(source_check.state == CheckState.CHECK_STATE_FINAL)

    # build check verifications ################################################
    build_check = turboci.get_check_view(graph,
                                         chromium_tests.BUILD_CHECK_ID).check
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
    analyze_check = turboci.get_check_view(graph, analyze_check_id).check
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
          [ORCHESTRATOR_ALL_TARGET_NAME],
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
      api.assert_turboci_graph(basic_assert_graph),
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
          [ORCHESTRATOR_ALL_TARGET_NAME],
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
          [ORCHESTRATOR_ALL_TARGET_NAME],
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
          [ORCHESTRATOR_ALL_TARGET_NAME],
      ),
      api.post_process(post_process.MustRun,
                       'archive src-side dep paths.archive src-side deps'),
      api.post_process(post_process.PropertiesContain, 'src_side_deps_digest'),
      api.post_process(post_process.PropertiesContain,
                       'src_side_test_spec_dir'),
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
          [ORCHESTRATOR_ALL_TARGET_NAME],
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
          [ORCHESTRATOR_ALL_TARGET_NAME],
      ),
      api.post_process(post_process.MustRun,
                       'archive src-side dep paths.archive src-side deps'),
      api.post_process(post_process.PropertiesContain, 'src_side_deps_digest'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'win_src_test_spec_dir_prop',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-compilator',
          revision='deadbeef',
      ),
      api.platform.name('win'),
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
      api.post_process(
          post_process.PropertyEquals,
          'src_side_test_spec_dir',
          'testing/buildbot',
      ),
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

  def without_patch_assert_graph(assert_, graph: GraphView):
    check_ids = set(c.check.identifier.id for c in graph.checks.values())
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
    source_check = turboci.get_check_view(
        graph, chromium_tests.WITHOUT_PATCH_SOURCE_CHECK_ID).check
    assert_(source_check.kind == CheckKind.CHECK_KIND_SOURCE)
    assert_(source_check.state == CheckState.CHECK_STATE_FINAL)

    # without patch build check verifications ##################################
    build_check = turboci.get_check_view(
        graph, chromium_tests.WITHOUT_PATCH_BUILD_CHECK_ID).check
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
      api.assert_turboci_graph(without_patch_assert_graph),
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
