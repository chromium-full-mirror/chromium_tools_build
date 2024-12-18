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
from PB.go.chromium.org.luci.resultdb.proto.v1 import test_result as test_result_pb2
from PB.go.chromium.org.luci.resultdb.proto.v1 import invocation as invocation_pb2
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
    'depot_tools/git',
    'depot_tools/gsutil',
    'gn',
    'isolate',
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
    'recipe_engine/time',
    'recipe_engine/resultdb',
    'recipe_engine/swarming',
]

WEB_TEST_EXTRA_ARGS = [
    '--no-retry-failures', '--isolated-script-test-output=test_result',
    '--wrapper=../../rr_tool/bin/rr record --output-trace-dir=../../trace_dir'
]
RUNNER_PACKAGE_PATH = 'rr_tool_runner'
PERNOSCO_REPO_PATH = 'pernosco'
UPLOAD_BUCKET = 'chromium-rr-traces'
TRACE_FILE = 'trace.tar'


def find_traces(api, target_path, invocation):
  """Find the first pass run trace and failed run trace from the input result.
  """
  pass_run_dir = ''
  failed_run_dir = ''
  if not api.path.exists(target_path):
    return pass_run_dir, failed_run_dir

  for i, test_result in enumerate(invocation.test_results):
    dir_path = target_path / str(i)
    test_trace_file = target_path / str(i) / TRACE_FILE
    if api.path.exists(test_trace_file):
      if test_result.status == test_result_pb2.PASS and not pass_run_dir:
        pass_run_dir = f'{dir_path}'
      if test_result.status != test_result_pb2.PASS and not failed_run_dir:
        failed_run_dir = f'{dir_path}'
  return pass_run_dir, failed_run_dir


def RunSteps(api, properties):
  if not any(
      test_info.test_suite for test_info in properties.target_test_infos):
    raise api.step.StepFailure('No test suites are being requested to run')

  builder_id, builder_config = api.chromium_polymorphic.lookup_builder_config(
      allow_tester=True)

  if builder_config.execution_mode == ctbc.TEST:
    builder_id = chromium.BuilderId.create_for_group(
        builder_config.parent_builder_group, builder_config.parent_buildername)

  source_dir, targets_config = _bot_update(api, builder_config)
  build_dir = api.chromium.default_build_dir(source_dir)

  tests = _create_tests(api, properties.target_test_infos, targets_config)

  raw_result = _compile(api, tests, source_dir, build_dir, builder_id)
  if raw_result.status != common_pb.SUCCESS:
    return raw_result

  runner_dir = source_dir / RUNNER_PACKAGE_PATH
  api.file.copytree('copy test runner', api.resource('.'), runner_dir)
  api.chromium.mb_isolate_everything(source_dir, build_dir, None)

  _isolate(api, tests, source_dir, build_dir, builder_config)

  swarming_tasks_and_test_infos = _create_tasks_and_test_infos(
      api, properties.target_test_infos, tests)

  task_results_and_test_infos = _collect_task_results(
      api, swarming_tasks_and_test_infos)

  _process_task_results(api, task_results_and_test_infos)


def _bot_update(api, builder_config):
  api.chromium_tests.configure_build(builder_config)
  update_results, _, targets_config = api.chromium_tests.prepare_checkout(
      builder_config, report_cache_state=False)

  # Checkout the pernosco repo for the test runner.
  api.git.checkout(
      url='https://chromium.googlesource.com/'
      'external/github.com/Pernosco/on-prem',
      dir_path=update_results.source_root.path / PERNOSCO_REPO_PATH,
      ref='refs/heads/upstream/main',
  )
  return update_results.source_root.path, targets_config


def _create_tests(api, target_test_infos, targets_config):
  test_suite_names = []
  for test_info in target_test_infos:
    if test_info.test_suite:
      test_suite_names.append(test_info.test_suite)

  # Create test objects for all input tests, removed test suite will not be
  # added here.
  tests = [t for t in targets_config.all_tests if t.name in test_suite_names]
  if not tests:
    raise api.step.StepFailure('No valid input test suites, please check if the'
                               ' input test suites are removed')
  return tests


def _compile(api, tests, source_dir, build_dir, builder_id):
  with api.chromium.guard_compile(build_dir):
    gn_args = api.chromium.mb_lookup(
        source_dir, builder_id, recursive=False, name='lookup_builder_gn_args')
    args = api.gn.parse_gn_args(gn_args)
    use_reclient = args.get('use_remoteexec') == 'true' and args.get(
        'use_reclient') != 'false'

    gn_args = gn_args.splitlines()
    # Update gn args with symbol_level=2 is required to get debug symbols for rr
    gn_args.append('symbol_level = 2')
    gn_args.append('forbid_non_component_debug_builds = false')
    gn_args.append('use_debug_fission = false')
    api.file.write_text('write gn args', build_dir.joinpath('args.gn'),
                        '\n'.join(gn_args))
    api.gn.gen(build_dir, 'gn_gen')

    targets = list(
        set(itertools.chain.from_iterable(t.compile_targets() for t in tests)))
    raw_result = api.chromium.compile(
        source_dir, build_dir, targets=targets, use_reclient=use_reclient)
    return raw_result


def _isolate(api, tests, source_dir, build_dir, builder_config):
  isolate_targets = [t.isolate_target for t in tests]
  for isolate_target in isolate_targets:
    file_path = build_dir.joinpath('%s.isolate' % isolate_target)
    api.isolate.add_files_to_isolate_file(
        file_path,
        [f'../../{RUNNER_PACKAGE_PATH}/', f'../../{PERNOSCO_REPO_PATH}/'])
  api.chromium_tests.isolate_tests(
      source_dir,
      build_dir,
      builder_config,
      tests,
      '',
      '',
  )


def _create_tasks_and_test_infos(api, target_test_infos, tests):
  cipd_packages = [
      chromium_swarming.CipdPackage.create(
          name='infra/3pp/tools/rr/${platform}',
          version='latest',
          root='rr_tool',
      )
  ]
  test_suite_to_tests = {t.name: t for t in tests}
  swarming_tasks_and_test_infos = []
  for test_info in target_test_infos:
    if test_info.test_suite not in test_suite_to_tests:
      continue
    test = test_suite_to_tests[test_info.test_suite]
    # Construct test cmd, trigger reproducing job in swarming.
    command = [
        'vpython3', f'../../{RUNNER_PACKAGE_PATH}/test_runner.py',
        '--test={0}'.format(test_info.test_name),
        '--output-dir={0}'.format('${ISOLATED_OUTDIR}'), '--'
    ]
    # TODO(jiesheng): Support other test type for rr test launcher.
    command.extend(test.raw_cmd)
    command.extend(WEB_TEST_EXTRA_ARGS)
    relative_cwd = str(test.relative_cwd)
    task_input = api.isolate.isolated_tests.get(test.isolate_target)

    task = api.chromium_swarming.task(
        name=f'rr tool runner for '
        f'{test_info.test_name} in {test_info.test_suite}',
        raw_cmd=command,
        cas_input_root=task_input,
        service_account=test.spec.service_account,
        relative_cwd=relative_cwd,
        cipd_packages=cipd_packages)

    task_slice = task.request[0]
    dimensions = {'pool': 'chromium.tests.rr', 'os': 'Linux'}
    task_dimensions = task_slice.dimensions
    task_dimensions.update(dimensions)
    task_slice = task_slice.with_dimensions(**task_dimensions)

    tags = {'test_suite': [test.canonical_name]}
    task.request = task.request.with_slice(0, task_slice).with_tags(tags)

    swarming_tasks_and_test_infos.append((task, test_info))
    api.chromium_swarming.trigger_task(task, resultdb=test.spec.resultdb)
  return swarming_tasks_and_test_infos


def _collect_task_results(api, swarming_tasks_and_test_infos):
  task_results_and_test_infos = []
  for task, test_info in swarming_tasks_and_test_infos:
    task_result, _ = api.chromium_swarming.collect_task(task)
    task_results_and_test_infos.append((task_result, test_info))
  return task_results_and_test_infos


def _process_task_results(api, task_results_and_test_infos):
  for i, (task_result, test_info) in enumerate(task_results_and_test_infos):
    shard_result = task_result.chromium_swarming.summary['shards'][0]
    # TODO(jiesheng): Update fetch_rdb_results in test_utils api to use
    # here to get back test results.
    inv_ids = api.resultdb.invocation_ids(
        [shard_result['resultdb_info']['invocation']])
    inv_map = api.resultdb.query(inv_ids)
    invocation = inv_map.get(inv_ids[0], None)
    cas_digest = shard_result.get('cas_output_root', {}).get('digest')
    if not cas_digest or not invocation:
      # TODO(jiesheng): Handle the missing output from task.
      continue

    download_dir = api.path.cleanup_dir / f'trace_dir_{i}'
    api.file.ensure_directory('ensure traces dir exist', download_dir)

    digest = '{}/{}'.format(cas_digest['hash'], cas_digest['size_bytes'])
    api.cas.download('download test traces', digest, download_dir)
    traces_out_dir = api.path.join(api.path.cleanup_dir, f'output_traces_{i}')
    found_test_traces = False
    for target_path in api.file.listdir(
        'listdir test dirs', download_dir, test_data=['test_name']):
      target_name = api.path.basename(target_path)
      pass_run_dir, failed_run_dir = find_traces(api, target_path, invocation)
      if failed_run_dir:
        # Pass Run trace is optional if failed run trace exists.
        if pass_run_dir:
          pass_run_new_dir = api.path.join(traces_out_dir, target_name,
                                           'pass_run_trace')
          api.file.ensure_directory('ensure pass trace dir exist',
                                    pass_run_new_dir)
          api.file.move('move pass trace', f'{pass_run_dir}/trace.tar',
                        pass_run_new_dir)
        failed_run_new_dir = api.path.join(traces_out_dir, target_name,
                                           'failed_run_trace')
        api.file.ensure_directory('ensure failed trace dir exist',
                                  failed_run_new_dir)
        api.file.move('move failed trace', f'{failed_run_dir}/trace.tar',
                      failed_run_new_dir)
        found_test_traces = True

    if found_test_traces:
      cur_date = api.time.utcnow().strftime('%Y-%m-%d-%H:%M:%S')
      if test_info.bug_id:
        cloud_folder_name = (f'{test_info.bug_id}/{test_info.test_suite}'
                             f'/test-rr-traces-{cur_date}')
      else:
        cloud_folder_name = f'test-rr-traces-{cur_date}'
      api.gsutil.upload(
          traces_out_dir,
          UPLOAD_BUCKET,
          cloud_folder_name,
          args=['-r'],
          link_name='Test rr traces')

    api.file.rmtree('rmtree %s' % download_dir, download_dir)

def GenTests(api):
  ctbc_api = api.chromium_tests_builder_config

  inv_bundle = {
      'task-example.swarmingserver.appspot.com-1234':
          api.resultdb.Invocation(
              proto=invocation_pb2.Invocation(
                  state=invocation_pb2.Invocation.FINALIZED),
              test_results=[
                  test_result_pb2.TestResult(
                      test_id='ninja://chromium/tests:browser_tests/',
                      expected=False,
                      status=test_result_pb2.FAIL,
                  ),
                  test_result_pb2.TestResult(
                      test_id='ninja://chromium/tests:browser_tests/',
                      expected=False,
                      status=test_result_pb2.PASS,
                  ),
              ],
          ),
  }

  def bad_summary_json():
    step_data = api.chromium_swarming.canned_summary_output_raw()
    step_data['shards'][0]['resultdb_info']['invocation'] = (
        'invocations/task-example.swarmingserver.appspot.com-1234')
    step_data['shards'][0]['cas_output_root']['digest'] = ''
    return step_data

  def good_summary_json():
    step_data = api.chromium_swarming.canned_summary_output_raw()
    step_data['shards'][0]['resultdb_info']['invocation'] = (
        'invocations/task-example.swarmingserver.appspot.com-1234')
    return step_data

  yield api.test(
      'happy_path_compile_test',
      api.buildbucket.try_build(
          project='fake-project', builder='fake-builder', build_number=1),
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
                      test_name='test1',
                  ),
                  InputProperties.TestInfo(
                      test_suite='blink_wpt_tests',
                      test_name='test2',
                      bug_id='123',
                  ),
                  InputProperties.TestInfo(
                      test_suite='blink_wpt_tests',
                      test_name='test3',
                  ),
                  InputProperties.TestInfo(
                      test_suite='blink_wpt_tests',
                      test_name='test4',
                  ),
                  InputProperties.TestInfo(
                      test_suite='blink_web_tests',
                      test_name='test3',
                  ),
                  InputProperties.TestInfo(
                      test_suite='blink_web_tests',
                      test_name='test4',
                  ),
              ],)),
      api.builder_group.for_current('chromium.fyi'),
      api.override_step_data(
          'Read [CACHE]/builder/src/out/Release/blink_wpt_tests.isolate',
          api.file.read_json({'cmd': ''})),
      api.step_data('rr tool runner for test1 in blink_wpt_tests',
                    api.chromium_swarming.summary(None, bad_summary_json())),
      api.step_data('rr tool runner for test2 in blink_wpt_tests',
                    api.chromium_swarming.summary(None, good_summary_json())),
      api.step_data('rr tool runner for test3 in blink_wpt_tests',
                    api.chromium_swarming.summary(None, good_summary_json())),
      api.step_data('rr tool runner for test4 in blink_wpt_tests',
                    api.chromium_swarming.summary(None, good_summary_json())),
      api.resultdb.query(step_name='rdb query', inv_bundle=inv_bundle),
      api.resultdb.query(step_name='rdb query (2)', inv_bundle=inv_bundle),
      api.resultdb.query(step_name='rdb query (3)', inv_bundle=inv_bundle),
      api.resultdb.query(step_name='rdb query (4)', inv_bundle=inv_bundle),
      api.path.files_exist(
          api.path.cleanup_dir / 'trace_dir_1' / 'test_name' / '0' /
          'test_result',
          api.path.cleanup_dir / 'trace_dir_1' / 'test_name' / '0' / TRACE_FILE,
          api.path.cleanup_dir / 'trace_dir_1' / 'test_name' / '1' /
          'test_result',
          api.path.cleanup_dir / 'trace_dir_1' / 'test_name' / '1' / TRACE_FILE,
          api.path.cleanup_dir / 'trace_dir_2' / 'test_name' / '0' /
          'test_result',
          api.path.cleanup_dir / 'trace_dir_2' / 'test_name' / '0' / TRACE_FILE,
          api.path.cleanup_dir / 'trace_dir_2' / 'test_name' / '1' /
          'test_result',
          api.path.cleanup_dir / 'trace_dir_2' / 'test_name' / '1' / TRACE_FILE,
      ),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'happy_path_compile_test_with_child_tester',
      api.buildbucket.try_build(
          project='fake-project', builder='fake-builder', build_number=1),
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
                      test_name='test1',
                  ),
                  InputProperties.TestInfo(
                      test_suite='blink_wpt_tests',
                      test_name='test2',
                  ),
              ],)),
      api.builder_group.for_current('chromium.fyi'),
      api.override_step_data(
          'Read [CACHE]/builder/src/out/Release/blink_wpt_tests.isolate',
          api.file.read_json({'cmd': ''})),
      api.step_data('rr tool runner for test1 in blink_wpt_tests',
                    api.chromium_swarming.summary(None, good_summary_json())),
      api.step_data('rr tool runner for test2 in blink_wpt_tests',
                    api.chromium_swarming.summary(None, good_summary_json())),
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
                      test_name='test1',
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
      api.expect_status('FAILURE'),
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
                      test_name='test1',
                  ),
              ],)),
      api.builder_group.for_current('chromium.fyi'),
      api.step_data('compile', retcode=1),
      api.expect_status('FAILURE'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'builder_gn_args_test',
      api.buildbucket.try_build(
          project='fake-project', builder='fake-builder', build_number=1),
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
                      test_name='test1',
                  ),
                  InputProperties.TestInfo(
                      test_suite='blink_wpt_tests',
                      test_name='test2',
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
      api.step_data('rr tool runner for test1 in blink_wpt_tests',
                    api.chromium_swarming.summary(None, good_summary_json())),
      api.step_data('rr tool runner for test2 in blink_wpt_tests',
                    api.chromium_swarming.summary(None, good_summary_json())),
      api.post_process(DropExpectation),
  )
