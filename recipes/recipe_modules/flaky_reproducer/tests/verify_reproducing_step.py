# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.recipe_api import Property
from RECIPE_MODULES.build.flaky_reproducer.libs import (ReproducingStep,
                                                        UnexpectedTestResult)

DEPS = [
    'flaky_reproducer',
    'recipe_engine/buildbucket',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/luci_analysis',
    'recipe_engine/properties',
    'recipe_engine/resultdb',
    'recipe_engine/step',
    'recipe_engine/swarming',
]

PROPERTIES = {
    'task_id': Property(default=None, kind=str),
    'failing_sample': Property(default=None),
    'reproducing_step_data': Property(default=None),
    'verify_on_builders': Property(default=None, kind=list),
    'monorail_issue': Property(default=None, kind=str),
}


def RunSteps(api, task_id, failing_sample, reproducing_step_data,
             verify_on_builders, monorail_issue):
  api.flaky_reproducer.set_config('auto')
  if reproducing_step_data:
    reproducing_step = ReproducingStep.from_jsonish(reproducing_step_data)
  else:
    reproducing_step = None
  builder_results = api.flaky_reproducer.verify_reproducing_step(
      task_id, failing_sample, reproducing_step, verify_on_builders)
  api.flaky_reproducer.summarize_results(
      task_id=task_id,
      failing_sample=failing_sample,
      test_binary=reproducing_step and reproducing_step.test_binary or None,
      reproducing_step=reproducing_step,
      all_reproducing_steps=([] if reproducing_step is None else
                             [reproducing_step]),
      builder_results=builder_results,
      monorail_issue=monorail_issue)


import re
from google.protobuf import timestamp_pb2, struct_pb2

from recipe_engine import post_process
from PB.go.chromium.org.luci.resultdb.proto.v1 import (
    common as common_pb2,  # go/pyformat-break
    invocation as invocation_pb2,  #
    resultdb as resultdb_pb2,  #
    test_result as test_result_pb2,  #
)
from PB.go.chromium.org.luci.analysis.proto.v1 import (
    common as analysis_common_pb2,  # go/pyformat-break
    test_history,  #
    test_verdict,  #
)


def GenTests(api):
  resultdb_invocation = api.resultdb.Invocation(
      proto=invocation_pb2.Invocation(
          state=invocation_pb2.Invocation.FINALIZED,
          realm='chromium:ci',
          create_time=timestamp_pb2.Timestamp(seconds=1658269605),
          finalize_time=timestamp_pb2.Timestamp(seconds=1658269605),
      ),
      test_results=[
          test_result_pb2.TestResult(
              test_id='ninja://base:base_unittests/MockUnitTests.FailTest',
              name='test_name_1',
              expected=False,
              status=test_result_pb2.FAIL,
              tags=[
                  common_pb2.StringPair(
                      key="test_name", value="MockUnitTests.FailTest"),
              ],
          ),
      ],
  )
  test_running_history = resultdb_pb2.QueryTestResultsResponse(test_results=[
      test_result_pb2.TestResult(
          test_id='ninja://base:base_unittests/MockUnitTests.FailTest',
          name=('invocations/task-example.swarmingserver.appspot.com'
                '-54321fffffabc001/result-1'),
          variant=common_pb2.Variant(**{'def': {
              'builder': 'Linux Tests',
          }}),
      ),
      test_result_pb2.TestResult(
          test_id='ninja://base:base_unittests/MockUnitTests.FailTest',
          name='unknown-name-format',
          variant=common_pb2.Variant(**{'def': {
              'builder': 'Linux Tests',
          }}),
      ),
  ])
  query_variants_res = test_history.QueryVariantsResponse(variants=[
      test_history.QueryVariantsResponse.VariantInfo(
          variant_hash='dummy_hash_1',
          variant=analysis_common_pb2.Variant(
              **{"def": {
                  'builder': 'Win10 Tests x64'
              }}),
      ),
  ])
  query_test_history_res = test_history.QueryTestHistoryResponse(verdicts=[
      test_verdict.TestVerdict(
          test_id='ninja://base:base_unittests/MockUnitTests.FailTest',
          variant_hash='dummy_hash_1',
          invocation_id='build-1234',
          status=test_verdict.TestVerdictStatus.EXPECTED,
      ),
  ])
  generate_bb_get_multi_result = lambda prop: {
      'id': 1234,
      'input': {
          'properties': {
              'fields': {
                  k: struct_pb2.Value(string_value=v) for k, v in prop.items()
              }
          }
      }
  }

  yield api.test(
      'cannot_retrieve_invocation',
      api.properties(
          task_id='some-task-id',
          failing_sample=UnexpectedTestResult('some-test'),
          reproducing_step_data=api.json.loads(
              api.flaky_reproducer.get_test_data('reproducing_step.json'))),
      api.expect_status('FAILURE'),
      api.post_check(post_process.ResultReason,
                     'Cannot retrieve invocation for task some-task-id.'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'cannot_find_test_result_for_test',
      api.properties(
          task_id='some-task-id',
          failing_sample=UnexpectedTestResult('Not.Exists.Test'),
          reproducing_step_data=api.json.loads(
              api.flaky_reproducer.get_test_data('reproducing_step.json'))),
      api.resultdb.query(
          {
              'task-example.swarmingserver.appspot.com-some-task-id':
                  resultdb_invocation,
          },
          step_name='verify_reproducing_step.find_related_builders.rdb query'),
      api.expect_status('FAILURE'),
      api.post_check(post_process.ResultReason,
                     'Cannot find TestResult for test Not.Exists.Test.'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'no_verify_builders_and_no_reproducing_step',
      api.properties(
          task_id='some-task-id',
          failing_sample=UnexpectedTestResult('MockUnitTests.FailTest')),
      api.post_check(lambda check, steps: check(
          steps['summarize_results'].step_summary_text,
          re.search(r"could NOT be reproduced.", steps['summarize_results'].
                    step_summary_text))),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'verify_builder_failed',
      api.properties(
          task_id='some-task-id',
          failing_sample=UnexpectedTestResult('MockUnitTests.FailTest'),
          reproducing_step_data=api.json.loads(
              api.flaky_reproducer.get_test_data('reproducing_step.json')),
      ),
      api.resultdb.query(
          {
              'task-example.swarmingserver.appspot.com-some-task-id':
                  resultdb_invocation,
          },
          step_name='verify_reproducing_step.find_related_builders.rdb query'),
      api.luci_analysis.query_variants(
          query_variants_res,
          test_id='ninja://base:base_unittests/MockUnitTests.FailTest',
          parent_step_name='verify_reproducing_step.find_related_builders',
      ),
      api.luci_analysis.query_test_history(
          query_test_history_res,
          test_id='ninja://base:base_unittests/MockUnitTests.FailTest',
          parent_step_name='verify_reproducing_step.find_related_builders',
      ),
      api.buildbucket.simulated_get_multi(
          builds=[generate_bb_get_multi_result({'$recipe_engine/cq': ''})],
          step_name='verify_reproducing_step.find_related_builders.buildbucket.get_multi',
      ),
      api.resultdb.query_test_results(
          test_running_history,
          step_name='verify_reproducing_step.find_related_builders.query_test_results',
      ),
      api.step_data(
          'verify_reproducing_step.get_test_binary from 54321fffffabc001',
          api.json.output_stream(
              api.json.loads(
                  api.flaky_reproducer.get_test_data(
                      'gtest_task_request.json')))),
      api.step_data(
          'verify_reproducing_step.get_test_binary from some-task-id',
          api.json.output_stream(
              api.json.loads(
                  api.flaky_reproducer.get_test_data(
                      'gtest_task_request.json')))),
      api.step_data(
          'verify_reproducing_step.collect verify results',
          api.swarming.collect([
              api.swarming.task_result(
                  '0', 'name', failure=True, output='failed-output'),
              api.swarming.task_result(
                  '1', 'name', failure=True, output='failed-output'),
          ])),
      api.post_check(lambda check, steps: check(
          steps['summarize_results'].step_summary_text,
          re.search(r"Linux Tests \(failing sample\).+with failure", steps[
              'summarize_results'].step_summary_text))),
      api.post_check(lambda check, steps: check(
          steps['summarize_results'].step_summary_text,
          re.search(r"Linux Tests.+with failure:", steps['summarize_results'].
                    step_summary_text))),
      api.post_check(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  verify_swarming_result = api.swarming.task_result(
      id='0',
      name='flaky reproducer verify on Linux Tests for MockUnitTests.FailTest',
      state=api.swarming.TaskState.COMPLETED,
      output='some-output',
      outputs=('result_summary_0.json',))
  gtest_empty_result = api.json.loads(
      api.flaky_reproducer.get_test_data('gtest_good_output.json'))
  gtest_empty_result['per_iteration_data'] = []
  yield api.test(
      'verify_not_reproducible',
      api.properties(
          task_id='some-task-id',
          failing_sample=UnexpectedTestResult('MockUnitTests.FailTest'),
          reproducing_step_data=api.json.loads(
              api.flaky_reproducer.get_test_data('reproducing_step.json')),
          verify_on_builders=['Win10 Tests x64'],
      ),
      api.resultdb.query(
          {
              'task-example.swarmingserver.appspot.com-some-task-id':
                  resultdb_invocation,
          },
          step_name='verify_reproducing_step.find_related_builders.rdb query'),
      api.luci_analysis.query_variants(
          query_variants_res,
          test_id='ninja://base:base_unittests/MockUnitTests.FailTest',
          parent_step_name='verify_reproducing_step.find_related_builders',
      ),
      api.luci_analysis.query_test_history(
          query_test_history_res,
          test_id='ninja://base:base_unittests/MockUnitTests.FailTest',
          parent_step_name='verify_reproducing_step.find_related_builders',
      ),
      api.buildbucket.simulated_get_multi(
          builds=[generate_bb_get_multi_result({'$recipe_engine/cq': ''})],
          step_name='verify_reproducing_step.find_related_builders.buildbucket.get_multi',
      ),
      api.resultdb.query_test_results(
          test_running_history,
          step_name='verify_reproducing_step.find_related_builders.query_test_results',
      ),
      api.step_data(
          'verify_reproducing_step.get_test_binary from 54321fffffabc001',
          api.json.output_stream(
              api.json.loads(
                  api.flaky_reproducer.get_test_data(
                      'gtest_task_request.json')))),
      api.step_data('verify_reproducing_step.collect verify results',
                    api.swarming.collect([verify_swarming_result])),
      api.step_data('verify_reproducing_step.load verify result',
                    api.file.read_json(gtest_empty_result)),
      api.post_check(lambda check, steps: check(
          steps['summarize_results'].step_summary_text,
          re.search(r"not reproduced", steps['summarize_results'].
                    step_summary_text))),
      api.post_check(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  bad_task_request = api.json.loads(
      api.flaky_reproducer.get_test_data('gtest_task_request.json'))
  bad_task_request['task_slices'][0]['properties']['command'] = [
      'unknown', 'wrapper'
  ]
  yield api.test(
      'unknown_test_binary_wrapper',
      api.properties(
          task_id='some-task-id',
          failing_sample=UnexpectedTestResult('MockUnitTests.FailTest'),
          reproducing_step_data=api.json.loads(
              api.flaky_reproducer.get_test_data('reproducing_step.json')),
      ),
      api.resultdb.query(
          {
              'task-example.swarmingserver.appspot.com-some-task-id':
                  resultdb_invocation,
          },
          step_name='verify_reproducing_step.find_related_builders.rdb query'),
      api.luci_analysis.query_variants(
          query_variants_res,
          test_id='ninja://base:base_unittests/MockUnitTests.FailTest',
          parent_step_name='verify_reproducing_step.find_related_builders',
      ),
      api.luci_analysis.query_test_history(
          query_test_history_res,
          test_id='ninja://base:base_unittests/MockUnitTests.FailTest',
          parent_step_name='verify_reproducing_step.find_related_builders',
      ),
      api.buildbucket.simulated_get_multi(
          builds=[generate_bb_get_multi_result({'$recipe_engine/cq': ''})],
          step_name='verify_reproducing_step.find_related_builders.buildbucket.get_multi',
      ),
      api.resultdb.query_test_results(
          test_running_history,
          step_name='verify_reproducing_step.find_related_builders.query_test_results',
      ),
      api.step_data(
          'verify_reproducing_step.get_test_binary from 54321fffffabc001',
          api.json.output_stream(bad_task_request)),
      api.post_check(lambda check, steps: check(
          steps['summarize_results'].step_summary_text,
          re.search(r"Not Supported test binary: unknown wrapper", steps[
              'summarize_results'].step_summary_text))),
      api.post_check(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  not_reproduced_step = api.json.loads(
      api.flaky_reproducer.get_test_data('reproducing_step.json'))
  not_reproduced_step['reproducing_rate'] = 0
  not_reproduced_step['reproduced_cnt'] = 0
  yield api.test(
      'report_not_reproduced_strategy',
      api.properties(
          task_id='some-task-id',
          failing_sample=UnexpectedTestResult('MockUnitTests.FailTest'),
          reproducing_step_data=not_reproduced_step),
      api.post_check(post_process.StatusSuccess),
      api.post_check(lambda check, steps: check(
          steps['summarize_results'].step_summary_text,
          re.search(r"strategy not reproduced", steps['summarize_results'].
                    step_summary_text))),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'no_invocations_found_for_related_builders',
      api.properties(
          task_id='some-task-id',
          failing_sample=UnexpectedTestResult('MockUnitTests.FailTest'),
          reproducing_step_data=api.json.loads(
              api.flaky_reproducer.get_test_data('reproducing_step.json')),
      ),
      api.resultdb.query(
          {
              'task-example.swarmingserver.appspot.com-some-task-id':
                  resultdb_invocation,
          },
          step_name='verify_reproducing_step.find_related_builders.rdb query'),
      api.luci_analysis.query_variants(
          test_history.QueryVariantsResponse(variants=[]),
          test_id='ninja://base:base_unittests/MockUnitTests.FailTest',
          parent_step_name='verify_reproducing_step.find_related_builders',
      ),
      api.post_check(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'verify nothing if no verify_on_builders matches',
      api.properties(
          task_id='some-task-id',
          failing_sample=UnexpectedTestResult('MockUnitTests.FailTest'),
          reproducing_step_data=api.json.loads(
              api.flaky_reproducer.get_test_data('reproducing_step.json')),
          verify_on_builders=['not_exists_builder'],
      ),
      api.resultdb.query(
          {
              'task-example.swarmingserver.appspot.com-some-task-id':
                  resultdb_invocation,
          },
          step_name='verify_reproducing_step.find_related_builders.rdb query'),
      api.luci_analysis.query_variants(
          query_variants_res,
          test_id='ninja://base:base_unittests/MockUnitTests.FailTest',
          parent_step_name='verify_reproducing_step.find_related_builders',
      ),
      api.post_check(post_process.StatusSuccess),
      api.post_check(lambda check, steps: check(
          'builder_results.json' not in steps['summarize_results'].logs)),
      api.post_process(post_process.DropExpectation),
  )
