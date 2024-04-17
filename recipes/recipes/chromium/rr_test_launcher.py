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
]

RUNNER_PACKAGE_PATH = 'rr_tool_runner'
TEST_BINARY_ISOLATE_FILENAME = 'runner.isolate'

def RunSteps(api):
  # TODO(jiesheng): Replace the current example build id and test id with top
  # 10 flaky tests from LUCI analysis. Support web test first, then gtests.
  task_id, test_name = (
      api.flaky_reproducer.query_resultdb_for_task_id_and_test_name(
          build_id="8752378832772530417",
          test_id='ninja://:blink_wpt_tests/external/wpt/css/'
          'css-transforms/z-index-does-not-apply.html'))

  test_binary_path = api.flaky_reproducer.get_test_binary(task_id)
  task_config = api.flaky_reproducer.get_test_binary_swarming_task_config(
      test_binary_path)
  api.cas.download('download test binary', task_config.cas_input_root,
                   api.path.cleanup_dir)
  runner_dir = api.path.cleanup_dir.join(RUNNER_PACKAGE_PATH)
  api.file.copytree('copy source files', api.resource('.'), runner_dir)
  api.isolate.write_isolate_file(
      runner_dir.join(TEST_BINARY_ISOLATE_FILENAME), ['../'])
  repacked_cas = api.isolate.isolate(
      'new test binary', runner_dir.join(TEST_BINARY_ISOLATE_FILENAME))

  # Trigger reproducing job in swarming.
  command = [
      'vpython3',
      'test_runner.py',
      '--test-names={0}'.format(test_name),
  ]

  request = (api.swarming.task_request().
      with_name("rr tool runner for {0}".format(test_name)).
      with_priority(200))

  dimensions = task_config.dimensions
  dimensions['pool'] = 'chromium.tests.rr'
  ensure_file = api.cipd.EnsureFile()
  ensure_file.add_package('infra/3pp/tools/rr/${platform}', 'latest', 'rr_tool')
  request_slice = (request[0].
      with_command(command).
      with_relative_cwd(RUNNER_PACKAGE_PATH).
      with_cas_input_root(repacked_cas).
      with_cipd_ensure_file(ensure_file).
      with_dimensions(**dimensions).
      with_execution_timeout_secs(1800).
      with_io_timeout_secs(1800).
      with_expiration_secs(1800))
  request = request.with_slice(0, request_slice)

  swarming_tasks = [api.swarming.trigger("rr tool runner", [request])[0]]
  api.swarming.collect(
      'collect rr tool runner results',
      swarming_tasks,
      output_dir=api.path.mkdtemp())


def GenTests(api):
  query_test_results = resultdb_pb2.QueryTestResultsResponse(
      test_results=[
          test_result_pb2.TestResult(
              test_id=('ninja://:blink_wpt_tests/external/wpt/'
                       'css/css-transforms/z-index-does-not-apply.html'),
              name=('invocations/task-example.swarmingserver.appspot.com'
                    '-task1/result-1'),
              expected=False,
              tags=[
                  common_pb2.StringPair(
                      key="test_name", value="MockUnitTests.FailTest"),
              ],
          ),
      ],)
  yield api.test(
      'happy_path',
      api.builder_group.for_current('chromium.fyi'),
      api.resultdb.query_test_results(query_test_results),
      api.step_data(
          'get_test_binary from task1',
          api.json.output_stream(
              api.json.loads(
                  api.flaky_reproducer.get_test_data(
                      'gtest_task_request.json')))),
      api.post_process(DropExpectation),
  )
