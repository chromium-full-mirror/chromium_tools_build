# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Compiles with patch and isolates tests"""

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from RECIPE_MODULES.build.chromium_tests_builder_config import try_spec
from PB.recipes.build.chromium.compilator import InputProperties
from PB.recipe_engine import result as result_pb2
from RECIPE_MODULES.build import chromium
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc
from RECIPE_MODULES.build.chromium_tests.api import (
    ALL_TEST_BINARIES_ISOLATE_NAME)
from RECIPE_MODULES.build.code_coverage.api import MAX_CANDIDATE_FILES
from recipe_engine import post_process
from PB.go.chromium.org.luci.resultdb.proto.v1 \
    import common as resultdb_common
from PB.go.chromium.org.luci.resultdb.proto.v1 \
    import test_result as test_result_pb2
from PB.go.chromium.org.luci.analysis.proto.v1 import test_history

DEPS = [
    'chromium',
    'chromium_checkout',
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
    orch_builder_id = chromium.BuilderId.create_for_group(
        builder_group, orchestrator)

    _, orch_builder_config = (
        api.chromium_tests_builder_config.lookup_builder(
            builder_id=orch_builder_id))

    api.chromium_tests.report_builders(orch_builder_config)

    rts_setting = properties.rts_setting

    # swarming_targets implies that this compilator build must be compiled
    # without a patch so that the orchestrator can retry these swarming tests
    # without patch
    if properties.swarming_targets:
      api.chromium_tests.configure_build(
          orch_builder_config,
          rts_setting,
      )
      api.chromium.apply_config('trybot_flavor')
      bot_update_step, targets_config = api.chromium_tests.prepare_checkout(
          orch_builder_config,
          timeout=3600,
          no_fetch_tags=True,
          enforce_fetch=True,
          patch=False,
          runhooks_suffix='without patch')

      # code coverage is ignored for without patch steps, but compile will
      # error if there is no files_to_instrument.txt file
      if api.code_coverage.using_coverage:
        api.code_coverage.src_dir = api.chromium_checkout.src_dir
        api.code_coverage.instrument([])

      # properties.swarming_targets should only be targets required for
      # isolated swarming tests, but a non-isolated swarming test could,
      # although rare, have a target_name that is also used by an isolated
      # swarming test. Checking for t.uses_isolate makes sure that we don't
      # include those non-isolated tests and end up running them too in this
      # build.
      test_suites = [
          t for t in targets_config.all_tests
          if t.target_name in properties.swarming_targets and t.uses_isolate
      ]
      raw_result, execution_info = (
          api.chromium_tests.build_and_isolate_failing_tests(
              orch_builder_id,
              orch_builder_config,
              test_suites,
              bot_update_step,
              'without patch',
              additional_compile_targets=[ORCHESTRATOR_ALL_TARGET_NAME],
              rts_setting=rts_setting))
    else:
      raw_result, task = api.chromium_tests.build_affected_targets(
          orch_builder_id,
          orch_builder_config,
          isolate_output_files_for_coverage=True,
          additional_compile_targets=[ORCHESTRATOR_ALL_TARGET_NAME],
          rts_setting=rts_setting)
      execution_info = task.swarming_execution_info
      test_suites = task.test_suites
      bot_update_step = task.bot_update_step

      # In case a without patch build is needed later, output the needed
      # deps override
      patch_root = bot_update_step.json.output['patch_root']
      if patch_root != bot_update_step.json.output['root']:
        deps_overrides = {}
        deps_revision_dict = bot_update_step.json.output['manifest'][patch_root]
        deps_overrides[patch_root] = deps_revision_dict['revision']
        output_deps_override = api.step.empty('output override_deps')
        output_deps_override.presentation.properties['override_deps'] = (
            deps_overrides)

    if raw_result and raw_result.status != common_pb.SUCCESS:
      return raw_result

    if any(t.uses_isolate for t in test_suites):
      affected_files_to_archive = []
      # If properties.swarming_targets exist, it means this build is doing a
      # "without patch" so there's no affected files to archive
      if (not properties.swarming_targets and
          not api.code_coverage.skipping_coverage):
        deleted_files = get_deleted_files(api, task.affected_files)
        affected_files_to_archive = [
            # In case this is a windows compilator
            str(api.path['checkout'].join(f)).replace('/', api.path.sep)
            for f in task.affected_files
            # If the affected file is deleted, don't attempt to archive it or
            # else you'll get a file not found error
            if f not in deleted_files
        ]
      archive_src_side_deps(api, affected_files_to_archive)

      # Isolate the tests first so the Orchestrator can trigger them asap
      trigger_properties = execution_info.ensure_command_lines_archived(
          api.chromium_tests).as_trigger_prop()

      properties_step = api.step('swarming trigger properties', [])
      properties_step.presentation.properties[
          'swarming_trigger_properties'] = trigger_properties
      properties_step.presentation.logs[
          'swarming_trigger_properties'] = api.m.json.dumps(
              trigger_properties, indent=2)

    non_isolated_tests = [t for t in test_suites if not t.uses_isolate]
    if non_isolated_tests:
      test_runner = api.chromium_tests.create_test_runner(
          non_isolated_tests,
          suffix='with patch',
      )
      with api.chromium_tests.wrap_chromium_tests(orch_builder_config,
                                                  non_isolated_tests):
        raw_result = test_runner()
        if raw_result and raw_result.status != common_pb.SUCCESS:
          return raw_result

      # check for new flaky tests on successful run w/ patch
      if api.flakiness.check_for_flakiness:
        new_tests = api.flakiness.find_tests_for_flakiness(
            non_isolated_tests, affected_files=task.affected_files)
        if new_tests:
          return api.chromium_tests.run_tests_for_flakiness(
              orch_builder_config, new_tests)

    return raw_result


def archive_src_side_deps(api, affected_files):
  """Archives src-side deps that the Orchestrator needs to run tests/coverage.

  Affected files is also needed by the orchestrator to run code coverage

  Args:
    affected_files (list): List of absolute string paths
  """
  with api.step.nest('archive src-side dep paths') as nested_step:
    # Dedupe in case a file from src_side_dep_paths is also an affected file
    dep_paths = sorted(set(get_src_side_dep_paths(api) + affected_files))

    # We need the files relative to the checkout dir so they can get downloaded
    # correctly on the orchestrator. And the .isolate file inherits the cwd of
    # the file itself, so create the file using a tmp name that should be
    # sufficiently unique to this build.
    isolate_file = api.path.join(
        api.path['checkout'], '%s_archive_deps.isolate' % api.swarming.task_id)
    rel_dep_paths = []
    for p in dep_paths:
      rel_dep_paths.append(api.path.relpath(p, api.path['checkout']))
    api.isolate.write_isolate_file(isolate_file, rel_dep_paths)
    digest = api.isolate.isolate('archive src-side deps', isolate_file)
    api.file.remove('rm %s' % isolate_file, isolate_file)

    relative_test_spec_dir = api.path.relpath(api.chromium.c.targets_spec_dir,
                                              api.path['checkout'])
    # On windows compilators, this would use a `\\` path separator instead of
    # a `/` that the linux orchestrators need to construct Paths
    relative_test_spec_dir = relative_test_spec_dir.replace(api.path.sep, '/')

    nested_step.presentation.properties['src_side_test_spec_dir'] = (
        relative_test_spec_dir)
    nested_step.presentation.properties['src_side_deps_digest'] = digest
    nested_step.presentation.logs['dep paths'] = api.json.dumps(
        dep_paths, indent=2)


def get_src_side_dep_paths(api):
  """Get src-side paths to archive.

  The chromium compile step writes which src-side deps to archive.
  The orchestrator build will use the CAS hash to download these deps to run
  tests and code coverage.

  Returns:
    List of string paths
  """
  dep_paths = set()
  runtime_deps_file = api.chromium.output_dir.join(
      ORCHESTRATOR_RUNTIME_DEPS_FILE)
  paths = (
      api.file.read_text('read orchestrator_all.runtime_deps',
                         runtime_deps_file).rstrip().split('\n'))
  for path in paths:
    # Paths written in these files look like '../../testing/X.py' relative
    # to the output dir
    file_path = api.path.relpath(
        api.chromium.output_dir.join(path), api.path['checkout'])
    file_path = api.path['checkout'].join(file_path)

    # Path can be a regex pattern
    if "*" in str(file_path):
      paths = api.file.glob_paths('get files that match pattern',
                                  api.path['checkout'], str(file_path))
      dep_paths.update([str(p) for p in paths])
    else:
      dep_paths.add(str(file_path))
  return list(dep_paths)


def get_deleted_files(api, affected_files):
  deleted_files = []
  for f in affected_files:
    path = api.path['checkout'].join(f)
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
                  api.buildbucket.build.infra.swarming.parent_run_id,
                  api.buildbucket.build.infra.swarming.hostname)))


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
                  regression_test_selection=try_spec.QUICK_RUN_ONLY,
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
            },
        })

  ctbc_api = api.chromium_tests_builder_config

  def ctbc_properties():
    return ctbc_api.properties(
        ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
            builder_group='fake-group',
            builder='fake-builder',
        ).with_mirrored_tester(
            builder_group='fake-group',
            builder='fake-tester',
        ).assemble())

  yield api.test(
      'basic',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-compilator',
          revision='deadbeef',
      ),
      api.platform.name('linux'),
      api.path.exists(api.path['checkout'].join('out/Release/browser_tests')),
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
      api.path.exists(api.path['checkout'].join('out/Release/browser_tests')),
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
      api.path.exists(api.path['checkout'].join('out/Release/browser_tests')),
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
          api.path['checkout'].join('foo.cc'),
          api.path['checkout'].join('testing/buildbot/chromium.linux.json'),
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
      api.path.exists(api.path['checkout'].join('out/Release/browser_tests')),
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
          [ALL_TEST_BINARIES_ISOLATE_NAME],
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
      api.path.exists(api.path['checkout'].join('out\\Release\\browser_tests')),
      api.code_coverage(use_clang_coverage=True),
      ctbc_properties(),
      api.properties(
          InputProperties(
              orchestrator=InputProperties.Orchestrator(
                  builder_name='fake-orchestrator',
                  builder_group='fake-try-group'))),
      override_test_spec(),
      api.tryserver.get_files_affected_by_patch(['foo.cc', 'bar/baz.cc']),
      api.path.exists(api.path['checkout'].join('foo.cc')),
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
      api.path.exists(api.path['checkout'].join('out/Release/browser_tests')),
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
      api.path.exists(api.path['checkout'].join('out\\Release\\browser_tests')),
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
      'quick run rts',
      api.properties(
          **{
              "$recipe_engine/cq": {
                  "active": True,
                  "dryRun": True,
                  "runMode": "QUICK_DRY_RUN",
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
              rts_setting='rts-chromium')),
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
          ['rts-chromium'],
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'quick run experimental rts',
      api.properties(
          **{
              "$recipe_engine/cq": {
                  "active": True,
                  "dryRun": True,
                  "runMode": "QUICK_DRY_RUN",
                  "topLevel": True
              }
          }),
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-compilator',
          experiments=['chromium_rts.experimental_model'],
          revision='deadbeef',
      ),
      api.chromium_tests_builder_config.databases(_TEST_BUILDERS,
                                                  _TEST_TRYBOTS),
      api.properties(
          InputProperties(
              orchestrator=InputProperties.Orchestrator(
                  builder_group='tryserver.chromium.test',
                  builder_name='rts-rel'),
              rts_setting='rts-ml-chromium')),
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
          ['rts-ml-chromium'],
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'quick run rts without_patch',
      api.properties(
          **{
              "$recipe_engine/cq": {
                  "active": True,
                  "dryRun": True,
                  "runMode": "QUICK_DRY_RUN",
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
              swarming_targets=['base_unittests']),
          rts_setting='rts-chromium'),
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
          ['rts-chromium'],
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
      api.platform.name('linux'),
      api.path.exists(api.path['checkout'].join('out/Release/browser_tests')),
      ctbc_properties(),
      api.properties(
          InputProperties(
              orchestrator=InputProperties.Orchestrator(
                  builder_name='fake-orchestrator',
                  builder_group='fake-try-group'))),
      override_test_spec(),
      api.runtime.global_shutdown_on_step('isolate tests (with patch)'),
      api.post_process(post_process.ResultReasonRE,
                       '.*causing this build to be canceled.*'),
      api.expect_status('CANCELED'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'without_patch',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-compilator',
          revision='deadbeef',
      ),
      api.platform.name('linux'),
      api.code_coverage(use_clang_coverage=True),
      api.path.exists(api.path['checkout'].join('out/Release/browser_tests')),
      ctbc_properties(),
      api.properties(
          InputProperties(
              orchestrator=InputProperties.Orchestrator(
                  builder_name='fake-orchestrator',
                  builder_group='fake-try-group'),
              swarming_targets=['browser_tests'])),
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
      api.path.exists(api.path['checkout'].join('out/Release/browser_tests')),
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
      api.path.exists(api.path['checkout'].join('out/Release/browser_tests')),
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
      api.post_process(post_process.ResultReasonRE,
                       '.*check_static_initializers.*'),
      api.post_process(post_process.MustRun, 'calculate flake rates'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )
