# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Recipe used to launch CI build with the rr tool.

The recipe will run top flaky tests using the rr tool, upload the recorded
traces to GCS.
"""

from recipe_engine.post_process import (DoesNotRun, DropExpectation,
                                        LogContains, ResultReason,
                                        StepCommandRE, StepFailure, StepSuccess)

from PB.go.chromium.org.luci.resultdb.proto.v1 import (common as common_pb2,
                                                       resultdb as resultdb_pb2,
                                                       test_result as
                                                       test_result_pb2)

DEPS = [
    'builder_group',
    'chromium',
    'chromium_tests',
    'depot_tools/gsutil',
    'flaky_reproducer',
    'isolate',
    'recipe_engine/cas',
    'recipe_engine/cipd',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/resultdb',
    'recipe_engine/step',
    'recipe_engine/swarming',
    'recipe_engine/time',
]

RUNNER_PACKAGE_PATH = 'rr_tool_runner'
TEST_BINARY_ISOLATE_FILENAME = 'runner.isolate'
UPLOAD_BUCKET = 'chromium-rr-traces'
TEST_SUITE_ALLOW_LIST = [
    'blink_wpt_tests', 'blink_web_tests',
    'not_site_per_process_blink_wpt_tests',
    'not_site_per_process_blink_web_tests', 'high_dpi_blink_wpt_tests',
    'high_dpi_blink_web_tests'
]
WEB_TEST_CMD = [
    'vpython3', 'third_party/blink/tools/run_web_tests.py', '-t', 'Release',
    '--no-retry-failures', '--driver-kill-timeout-secs=10',
    '--isolated-script-test-output=test_result',
    '--wrapper=rr_tool/bin/rr record --output-trace-dir=trace_dir'
]


def find_traces(api, target_path):
  # TODO(jiesheng): Support different test type's result file check.
  pass_run_dir = ''
  failed_run_dir = ''
  if not api.path.exists(target_path):
    return pass_run_dir, failed_run_dir

  for dir_path in api.file.listdir(
      'listdir traces', target_path, test_data=['0', '1']):
    dir_base_name = api.path.basename(dir_path)
    test_result_file = dir_path / 'test_result'
    test_trace_file = dir_path / 'trace.tar'
    if api.path.exists(test_result_file) and api.path.exists(test_trace_file):
      test_data = api.file.read_json(f'read test result for {dir_base_name}',
                                     test_result_file)
      if test_data['num_passes'] > 0 and not pass_run_dir:
        pass_run_dir = f'{dir_path}'
      if test_data['num_passes'] == 0 and not failed_run_dir:
        failed_run_dir = f'{dir_path}'
  return pass_run_dir, failed_run_dir

def RunSteps(api):
  # Select tests from past 10 days' bugs.
  cmd = [
      'vpython3',
      api.resource('test_selection.py'), '--output-json',
      api.json.output(), '--sample-day=10'
  ]
  step_result = api.step('query test data', cmd)
  test_infos = step_result.json.output

  # Prepare rr tool binary
  ensure_file = api.cipd.EnsureFile()
  ensure_file.add_package('infra/3pp/tools/rr/${platform}', 'latest', 'rr_tool')
  recorded_tasks_to_config = {}
  swarming_tasks = []
  for test_info in test_infos:
    # TODO(jiesheng): Support other test type for rr test launcher.
    if test_info.get('test_suite', '') not in TEST_SUITE_ALLOW_LIST:
      continue
    build_id = test_info.get('invocation_id', '').split('-')[-1]
    test_id = test_info.get('test_id', '')
    if not build_id or not test_id:
      continue

    task_id, test_name = (
        api.flaky_reproducer.query_resultdb_for_task_id_and_test_name(
            build_id=build_id, test_id=test_id))
    task_path = api.path.cleanup_dir / task_id
    if task_id in recorded_tasks_to_config:
      task_config, repacked_cas = recorded_tasks_to_config[task_id]
    else:
      test_binary_path = api.flaky_reproducer.get_test_binary(task_id)
      task_config = api.flaky_reproducer.get_test_binary_swarming_task_config(
          test_binary_path)
      api.cas.download('download test binary', task_config.cas_input_root,
                       task_path)
      runner_dir = task_path / RUNNER_PACKAGE_PATH
      api.file.copytree('copy source files', api.resource('.'), runner_dir)
      api.isolate.write_isolate_file(runner_dir / TEST_BINARY_ISOLATE_FILENAME,
                                     ['../'])
      repacked_cas = api.isolate.isolate(
          'new test binary', runner_dir / TEST_BINARY_ISOLATE_FILENAME)
      recorded_tasks_to_config[task_id] = task_config, repacked_cas

    # Construct test cmd, trigger reproducing job in swarming.
    command = [
        'vpython3', 'test_runner.py', '--test={0}'.format(test_name),
        '--output-dir={0}'.format('${ISOLATED_OUTDIR}'), '--'
    ]
    # TODO(jiesheng): Construct test command based on test type.
    command.extend(WEB_TEST_CMD)
    request = (
        api.swarming.task_request().with_name(
            "rr tool runner for {0}".format(test_name)).with_priority(200))

    dimensions = task_config.dimensions
    dimensions['pool'] = 'chromium.tests.rr'

    request_slice = (
        request[0].with_command(command).with_relative_cwd(RUNNER_PACKAGE_PATH)
        .with_cas_input_root(repacked_cas).with_cipd_ensure_file(ensure_file)
        .with_dimensions(**dimensions).with_execution_timeout_secs(
            1800).with_io_timeout_secs(1800).with_expiration_secs(1800))
    request = request.with_slice(0, request_slice)

    swarming_tasks.append(api.swarming.trigger("rr tool runner", [request])[0])

  # Collect all task result and upload to GCS
  task_results = api.swarming.collect(
      'collect rr tool runner results',
      swarming_tasks,
      output_dir=api.path.mkdtemp())

  for task_result in task_results:
    if not task_result.cas_outputs:
      # TODO(jiesheng): Handle the missing output from task.
      continue
    download_dir = api.path.cleanup_dir / f'trace_dir_{task_result.id}'
    api.file.ensure_directory('ensure traces dir exist', download_dir)
    api.cas.download('download test traces', task_result.cas_outputs.digest,
                     download_dir)

    traces_out_dir = api.path.join(api.path.cleanup_dir,
                                   f'output_traces_{task_result.id}')
    found_test_traces = False
    for target_path in api.file.listdir(
        'listdir test dirs', download_dir, test_data=['test_name']):
      target_name = api.path.basename(target_path)
      pass_run_dir, failed_run_dir = find_traces(api, target_path)
      if pass_run_dir and failed_run_dir:
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
      cloud_folder_name = f'test-rr-traces-{cur_date}'
      api.gsutil.upload(
          traces_out_dir,
          UPLOAD_BUCKET,
          cloud_folder_name,
          args=['-r'],
          link_name='Test rr traces')

def GenTests(api):
  query_test_results = resultdb_pb2.QueryTestResultsResponse(
      test_results=[
          test_result_pb2.TestResult(
              test_id='test_id_123',
              name=('invocations/task-example.swarmingserver.appspot.com'
                    '-task1/result-1'),
              expected=False,
              tags=[
                  common_pb2.StringPair(
                      key="test_name", value="MockUnitTests.FailTest"),
              ],
          ),
      ],)
  query_test_results_2 = resultdb_pb2.QueryTestResultsResponse(
      test_results=[
          test_result_pb2.TestResult(
              test_id='test_id_234',
              name=('invocations/task-example.swarmingserver.appspot.com'
                    '-task1/result-2'),
              expected=False,
              tags=[
                  common_pb2.StringPair(
                      key="test_name_2", value="MockUnitTests.FailTest"),
              ],
          ),
      ],)
  yield api.test(
      'happy_path',
      api.builder_group.for_current('chromium.fyi'),
      api.override_step_data(
          'query test data',
          api.json.output([{
              'test_suite': 'blink_wpt_tests',
              'invocation_id': 'build-123',
              'test_id': 'test_id_123'
          }, {
              'test_suite': 'blink_wpt_tests',
              'invocation_id': 'build-123',
              'test_id': 'test_id_234'
          }])),
      api.resultdb.query_test_results(query_test_results),
      api.step_data(
          'get_test_binary from task1',
          api.json.output_stream(
              api.json.loads(
                  api.flaky_reproducer.get_test_data(
                      'gtest_task_request.json')))),
      api.resultdb.query_test_results(
          query_test_results_2, step_name='query_test_results (2)'),
      api.path.exists(api.path.cleanup_dir / 'trace_dir_0' / 'test_name'),
      api.path.files_exist(
          api.path.cleanup_dir / 'trace_dir_0' / 'test_name' / '0' /
          'test_result',
          api.path.cleanup_dir / 'trace_dir_0' / 'test_name' / '0' /
          'trace.tar',
          api.path.cleanup_dir / 'trace_dir_0' / 'test_name' / '1' /
          'test_result',
          api.path.cleanup_dir / 'trace_dir_0' / 'test_name' / '1' /
          'trace.tar',
      ),
      api.override_step_data('read test result for 0',
                             api.file.read_json({'num_passes': 1})),
      api.override_step_data('read test result for 1',
                             api.file.read_json({'num_passes': 0})),
      api.post_process(DropExpectation),
  )
  yield api.test(
      'no_result',
      api.builder_group.for_current('chromium.fyi'),
      api.override_step_data(
          'query test data',
          api.json.output([{
              'test_suite': 'blink_wpt_tests',
              'invocation_id': 'build-123',
              'test_id': 'test_id_123'
          }])),
      api.resultdb.query_test_results(query_test_results),
      api.step_data(
          'get_test_binary from task1',
          api.json.output_stream(
              api.json.loads(
                  api.flaky_reproducer.get_test_data(
                      'gtest_task_request.json')))),
      api.post_process(DropExpectation),
  )
  yield api.test(
      'no_suite',
      api.builder_group.for_current('chromium.fyi'),
      api.override_step_data(
          'query test data',
          api.json.output([{
              'invocation_id': 'build-123',
              'test_id': 'test_id_123'
          }])),
      api.post_process(DropExpectation),
  )
  yield api.test(
      'no_build_id',
      api.builder_group.for_current('chromium.fyi'),
      api.override_step_data(
          'query test data',
          api.json.output([{
              'test_suite': 'blink_wpt_tests',
              'test_id': 'test_id_123'
          }])),
      api.post_process(DropExpectation),
  )
  yield api.test(
      'no_cas_output',
      api.builder_group.for_current('chromium.fyi'),
      api.override_step_data(
          'query test data',
          api.json.output([{
              'test_suite': 'blink_wpt_tests',
              'invocation_id': 'build-123',
              'test_id': 'test_id_123'
          }])),
      api.resultdb.query_test_results(query_test_results),
      api.step_data(
          'get_test_binary from task1',
          api.json.output_stream(
              api.json.loads(
                  api.flaky_reproducer.get_test_data(
                      'gtest_task_request.json')))),
      api.step_data(
          'collect rr tool runner results',
          api.swarming.collect([{
              'output': 'hello world!',
              'results': {
                  'exit_code': '0',
                  'name': 'corpus tests (reverse byte order)',
                  'state': 'COMPLETED',
                  'task_id': '2',
              },
          }])),
      api.post_process(DropExpectation),
  )
