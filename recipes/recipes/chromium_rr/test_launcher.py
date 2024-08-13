# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Recipe used to launch test runner using the rr tool.

The recipe will compile input tests, and execute test runner script to run tests
using the rr tool, and upload the recorded traces to GCS.
"""

import itertools
from recipe_engine.post_process import DropExpectation, MustRun
from RECIPE_MODULES.build import chromium
from RECIPE_MODULES.build import chromium_swarming
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipes.build.chromium_rr.test_launcher import InputProperties

PROPERTIES = InputProperties

DEPS = [
    'builder_group',
    'chromium',
    'chromium_checkout',
    'chromium_polymorphic',
    'chromium_swarming',
    'chromium_tests',
    'chromium_tests_builder_config',
    'depot_tools/gclient',
    'gn',
    'isolate',
    'depot_tools/git',
    'recipe_engine/buildbucket',
    'recipe_engine/cas',
    'recipe_engine/cipd',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/step',
]

WEB_TEST_EXTRA_ARGS = [
    '--no-retry-failures', '--isolated-script-test-output=test_result',
    '--wrapper=../../rr_tool/bin/rr record --output-trace-dir=../../trace_dir'
]
RUNNER_PACKAGE_PATH = 'rr_tool_runner'


def RunSteps(api, properties):

  test_suites = []
  for test_info in properties.target_test_infos:
    if test_info.test_suite:
      test_suites.append(test_info.test_suite)

  if not test_suites:
    return

  builder_id, builder_config = api.chromium_polymorphic.lookup_builder_config(
      allow_tester=True)
  if builder_config.execution_mode == ctbc.TEST:
    builder_id = chromium.BuilderId.create_for_group(
        builder_config.parent_builder_group, builder_config.parent_buildername)
  api.chromium_tests.configure_build(builder_config)
  update_step, _, targets_config = api.chromium_tests.prepare_checkout(
      builder_config, report_cache_state=False)

  # Create test objects for all input tests, removed test suite will not be
  # added here.
  tests = [t for t in targets_config.all_tests if t.name in test_suites]
  if not tests:
    raise api.step.StepFailure('No valid input test suites, please check if the'
                               ' input test suites are removed')
  targets = list(set(itertools.chain(*[t.compile_targets() for t in tests])))

  api.chromium.output_dir = update_step.source_root.path.joinpath(
      'out', api.chromium.c.build_config_fs)
  source_dir = update_step.source_root.path
  build_dir = api.chromium.default_build_dir(source_dir)
  with api.chromium.guard_compile(build_dir):
    # Update gn args with symbol_level=2
    gn_args = api.chromium.mb_lookup(
        source_dir, builder_id, recursive=False, name='lookup_builder_gn_args')
    args = api.gn.parse_gn_args(gn_args)
    use_reclient = args.get('use_remoteexec') == 'true' and args.get(
        'use_reclient') != 'false'

    gn_args = gn_args.splitlines()
    gn_args.append('symbol_level = 2')
    gn_args.append('forbid_non_component_debug_builds = false')
    gn_args.append('use_debug_fission = false')
    api.file.write_text('write gn args', build_dir.joinpath('args.gn'),
                        '\n'.join(gn_args))
    api.gn.gen(build_dir, 'gn_gen')

    raw_result = api.chromium.compile(
        source_dir, build_dir, targets=targets, use_reclient=use_reclient)
    if raw_result.status != common_pb.SUCCESS:
      return raw_result

  runner_dir = source_dir / RUNNER_PACKAGE_PATH
  api.file.copytree('copy source files', api.resource('.'), runner_dir)
  api.chromium.mb_isolate_everything(source_dir, build_dir, None)

  isolate_targets = [t.isolate_target for t in tests]
  for isolate_target in isolate_targets:
    file_path = build_dir.joinpath('%s.isolate' % isolate_target)
    api.isolate.add_files_to_isolate_file(file_path,
                                          [f'../../{RUNNER_PACKAGE_PATH}/'])
  api.chromium_tests.isolate_tests(
      source_dir,
      build_dir,
      builder_config,
      tests,
      '',
      '',
  )

  test_suite_to_tests = {t.name: t for t in tests}
  cipd_packages = [
      chromium_swarming.CipdPackage.create(
          name='infra/3pp/tools/rr/${platform}',
          version='latest',
          root='rr_tool',
      )
  ]
  swarming_tasks = []
  for test_info in properties.target_test_infos:
    if test_info.test_suite not in test_suite_to_tests:
      continue
    test = test_suite_to_tests[test_info.test_suite]
    for test_name in test_info.test_names:
      # Construct test cmd, trigger reproducing job in swarming.
      command = [
          'vpython3', f'../../{RUNNER_PACKAGE_PATH}/test_runner.py',
          '--test={0}'.format(test_name),
          '--output-dir={0}'.format('${ISOLATED_OUTDIR}'), '--'
      ]
      # TODO(jiesheng): Support other test type for rr test launcher.
      command.extend(test.raw_cmd)
      command.extend(WEB_TEST_EXTRA_ARGS)
      relative_cwd = str(test.relative_cwd)
      dimensions = {'pool': 'chromium.tests.rr', 'os': 'Linux'}
      task_input = api.isolate.isolated_tests.get(test.isolate_target)

      task = api.chromium_swarming.task(
          name=f'rr tool runner for {test_name}',
          raw_cmd=command,
          cas_input_root=task_input,
          service_account=test.spec.service_account,
          relative_cwd=relative_cwd,
          cipd_packages=cipd_packages)

      task_slice = task.request[0]
      task_dimensions = task_slice.dimensions
      task_dimensions.update(dimensions)
      task_slice = task_slice.with_dimensions(**task_dimensions)
      task.request = task.request.with_slice(0, task_slice)

      swarming_tasks.append(task)
      api.chromium_swarming.trigger_task(task)

  # Collect all task result
  for task in swarming_tasks:
    api.chromium_swarming.collect_task(task)

  # TODO(jiesheng): Upload trace result to GCS.


def GenTests(api):
  ctbc_api = api.chromium_tests_builder_config

  yield api.test(
      'happy_path_compile_test',
      api.chromium_polymorphic.triggered_properties(
          project='fake-project',
          bucket='fake-bucket',
          builder='fake-builder',
          builder_group='fake-group',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder='fake-builder',
              builder_group='fake-group',
          ).assemble()),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'name': 'blink_wpt_tests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                              'pool': 'fake-pool',
                          },
                          'service_account': 'test_account',
                      },
                  }],
              },
          }),
      api.properties(
          InputProperties(
              target_test_infos=[
                  InputProperties.TestInfo(
                      test_suite='blink_wpt_tests',
                      test_names=['test1', 'test2'],
                  ),
                  InputProperties.TestInfo(
                      test_suite='blink_web_tests',
                      test_names=['test3', 'test4'],
                  ),
              ],)),
      api.builder_group.for_current('chromium.fyi'),
      api.override_step_data(
          'Read [CACHE]/builder/src/out/Release/blink_wpt_tests.isolate',
          api.file.read_json({'cmd': ''})),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'happy_path_compile_test_with_child_tester',
      api.chromium_polymorphic.triggered_properties(
          project='fake-project',
          bucket='fake-bucket',
          builder='fake-tester',
          builder_group='fake-group',
      ),
      ctbc_api.ci_build(
          builder_group='fake-group',
          builder='fake-tester',
          builder_db=ctbc.BuilderDatabase.create({
              'fake-group': {
                  'fake-builder':
                      ctbc.BuilderSpec.create(
                          chromium_config='chromium',
                          gclient_config='chromium',
                      ),
                  'fake-tester':
                      ctbc.BuilderSpec.create(
                          execution_mode=ctbc.TEST,
                          parent_buildername='fake-builder',
                          parent_builder_group='fake-group',
                          chromium_config='chromium',
                          gclient_config='chromium',
                      ),
              },
          })),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-tester': {
                  'gtest_tests': [{
                      'name': 'blink_wpt_tests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                              'pool': 'fake-pool',
                          },
                          'service_account': 'test_account',
                      },
                  }],
              },
          }),
      api.properties(
          InputProperties(
              target_test_infos=[
                  InputProperties.TestInfo(
                      test_suite='blink_wpt_tests',
                      test_names=['test1', 'test2'],
                  ),
              ],)),
      api.builder_group.for_current('chromium.fyi'),
      api.override_step_data(
          'Read [CACHE]/builder/src/out/Release/blink_wpt_tests.isolate',
          api.file.read_json({'cmd': ''})),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'invaild_input_test_suite',
      api.chromium_polymorphic.triggered_properties(
          project='fake-project',
          bucket='fake-bucket',
          builder='fake-builder',
          builder_group='fake-group',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder='fake-builder',
              builder_group='fake-group',
          ).assemble()),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'name': 'blink_wpt_tests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                              'pool': 'fake-pool',
                          },
                          'service_account': 'test_account',
                      },
                  }],
              },
          }),
      api.properties(
          InputProperties(
              target_test_infos=[
                  InputProperties.TestInfo(
                      test_suite='blink_web_tests',
                      test_names=['test1', 'test2'],
                  ),
              ],)),
      api.builder_group.for_current('chromium.fyi'),
      api.expect_status('FAILURE'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'no_input',
      api.chromium_polymorphic.triggered_properties(
          project='fake-project',
          bucket='fake-bucket',
          builder='fake-builder',
          builder_group='fake-group',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder='fake-builder',
              builder_group='fake-group',
          ).assemble()),
      api.builder_group.for_current('chromium.fyi'),
      api.expect_status('SUCCESS'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'compile_with_failure',
      api.chromium_polymorphic.triggered_properties(
          project='fake-project',
          bucket='fake-bucket',
          builder='fake-builder',
          builder_group='fake-group',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder='fake-builder',
              builder_group='fake-group',
          ).assemble()),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'name': 'blink_wpt_tests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                              'pool': 'fake-pool',
                          },
                          'service_account': 'test_account',
                      },
                  }],
              },
          }),
      api.properties(
          InputProperties(
              target_test_infos=[
                  InputProperties.TestInfo(
                      test_suite='blink_wpt_tests',
                      test_names=['test1', 'test2'],
                  ),
              ],)),
      api.builder_group.for_current('chromium.fyi'),
      api.step_data('compile', retcode=1),
      api.expect_status('FAILURE'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'builder_gn_args_test',
      api.chromium_polymorphic.triggered_properties(
          project='fake-project',
          bucket='fake-bucket',
          builder='fake-builder',
          builder_group='fake-group',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder='fake-builder',
              builder_group='fake-group',
          ).assemble()),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'name': 'blink_wpt_tests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                              'pool': 'fake-pool',
                          },
                          'service_account': 'test_account',
                      },
                  }],
              },
          }),
      api.properties(
          InputProperties(
              target_test_infos=[
                  InputProperties.TestInfo(
                      test_suite='blink_wpt_tests',
                      test_names=['test1', 'test2'],
                  ),
              ],)),
      api.builder_group.for_current('chromium.fyi'),
      api.step_data(
          'lookup_builder_gn_args',
          stdout=api.raw_io.output_text('import("//builder.args")\n'
                                        'symbol_level = "1"\n'
                                        'a = true\n'
                                        'b = true')),
      api.override_step_data(
          'Read [CACHE]/builder/src/out/Release/blink_wpt_tests.isolate',
          api.file.read_json({'cmd': ''})),
      api.post_process(MustRun, 'write gn args'),
      api.post_process(DropExpectation),
  )
