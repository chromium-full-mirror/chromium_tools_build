# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.recipe_api import Property

DEPS = [
    'flaky_reproducer',
    'recipe_engine/buildbucket',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/luci_analysis',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/resultdb',
    'recipe_engine/swarming',
]

PROPERTIES = {
    'task_id': Property(default=None, kind=str),
    'build_id': Property(default=None, kind=int),
    'test_name': Property(default=None, kind=str),
    'test_id': Property(default=None, kind=str),
    'config': Property(default=None, kind=str),
    'monorail_issue': Property(default=None, kind=str),
}


def RunSteps(api, config, task_id, build_id, test_name, test_id,
             monorail_issue):
  api.flaky_reproducer.set_config(config)
  return api.flaky_reproducer.run(
      task_id=task_id,
      build_id=build_id,
      test_name=test_name,
      test_id=test_id,
      monorail_issue=monorail_issue)


from google.protobuf import timestamp_pb2, struct_pb2

from recipe_engine.post_process import (DropExpectation, StatusSuccess,
                                        ResultReason)
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
  success_swarming_results = [
      api.swarming.task_result(
          id='0',
          name='flaky reproducer strategy repeat for MockUnitTests.FailTest',
          state=api.swarming.TaskState.COMPLETED,
          output='some-output',
          outputs=('reproducing_step.json',)),
      api.swarming.task_result(
          id='1',
          name='flaky reproducer strategy batch for MockUnitTests.FailTest',
          state=api.swarming.TaskState.COMPLETED,
          output='some-output',
          outputs=()),
  ]
  failed_swarming_results = [
      api.swarming.task_result(
          id='0',
          name='flaky reproducer strategy repeat for MockUnitTests.FailTest',
          state=api.swarming.TaskState.TIMED_OUT,
          output='some-output',
          outputs=('reproducing_step.json',)),
      api.swarming.task_result(
          id='1',
          name='flaky reproducer strategy batch for MockUnitTests.FailTest',
          state=api.swarming.TaskState.COMPLETED,
          failure=True,
          output='some-output',
          outputs=()),
  ]
  resultdb_invocation = api.resultdb.Invocation(
      proto=invocation_pb2.Invocation(
          state=invocation_pb2.Invocation.FINALIZED,
          realm='chromium:ci',
      ),
      test_results=[
          test_result_pb2.TestResult(
              test_id='ninja://base:base_unittests/MockUnitTests.FailTest',
              name='test_name_1',
              expected=False,
              status=test_result_pb2.FAIL,
              start_time=timestamp_pb2.Timestamp(seconds=1658269605),
              variant=common_pb2.Variant(
                  **{
                      'def': {
                          'builder': 'Linux Tests',
                          'test_suite': 'base_unittests',
                      }
                  }),
              tags=[
                  common_pb2.StringPair(
                      key="test_name", value="MockUnitTests.FailTest"),
              ],
          ),
      ],
  )
  query_variants_res = test_history.QueryVariantsResponse(variants=[
      test_history.QueryVariantsResponse.VariantInfo(
          variant_hash='dummy_hash_1',
          variant=analysis_common_pb2.Variant(
              **{"def": {
                  'builder': 'Win10 Tests x64'
              }}),
      ),
      test_history.QueryVariantsResponse.VariantInfo(
          variant_hash='dummy_hash_2',
          variant=analysis_common_pb2.Variant(
              **{"def": {
                  'builder': 'Mac11 Tests'
              }}),
      ),
      test_history.QueryVariantsResponse.VariantInfo(
          variant_hash='dummy_hash_3',
          variant=analysis_common_pb2.Variant(
              **{"def": {
                  'builder': 'Linux Tests'
              }}),
      ),
      test_history.QueryVariantsResponse.VariantInfo(
          variant_hash='dummy_hash_4',
          variant=analysis_common_pb2.Variant(
              **{"def": {
                  'builder': 'Not Supported Builder'
              }}),
      ),
  ])
  query_test_history_res = test_history.QueryTestHistoryResponse(verdicts=[
      test_verdict.TestVerdict(
          test_id='ninja://base:base_unittests/MockUnitTests.FailTest',
          variant_hash='dummy_hash_1',
          invocation_id='build-1234',
          status=test_verdict.TestVerdictStatus.UNEXPECTED,
      ),
      test_verdict.TestVerdict(
          test_id='ninja://base:base_unittests/MockUnitTests.FailTest',
          variant_hash='dummy_hash_2',
          invocation_id='build-1233',
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
  verify_swarming_result = lambda id: api.swarming.task_result(
      id=id,
      name='flaky reproducer verify on Linux Tests for MockUnitTests.FailTest',
      state=api.swarming.TaskState.COMPLETED,
      output='some-output',
      outputs=('result_summary_0.json', 'result_summary_1.json'))
  issue_result = {
      "name":
          "projects/chromium/issues/123",
      "labels": [{
          "derivation": "EXPLICIT",
          "label": "Restrict-View-Google"
      }, {
          "derivation": "EXPLICIT",
          "label": "CV-M1"
      }],
  }
  yield api.test(
      'happy_path',
      api.properties(
          task_id='54321fffffabc123',
          test_name='MockUnitTests.FailTest',
          config='manual',
          monorail_issue='123'),
      api.step_data(
          'check_monorail_comment_posted'
          '.GetIssue projects/chromium/issues/123',
          api.json.output_stream(issue_result),
      ),
      api.step_data(
          'get_test_result_summary.download swarming outputs',
          api.raw_io.output_dir({
              'output.json':
                  api.flaky_reproducer.get_test_data('gtest_good_output.json'),
          })),
      api.step_data(
          'get_test_binary from 54321fffffabc123',
          api.json.output_stream(
              api.json.loads(
                  api.flaky_reproducer.get_test_data(
                      'gtest_task_request.json')))),
      api.step_data('collect strategy results',
                    api.swarming.collect(success_swarming_results)),
      api.step_data(
          ('collect_strategy_results'
           '.flaky reproducer strategy repeat for MockUnitTests.FailTest'
           '.load ReproducingStep'),
          api.file.read_json(
              api.json.loads(
                  api.flaky_reproducer.get_test_data(
                      'reproducing_step.json')))),
      api.resultdb.query(
          {
              'task-example.swarmingserver.appspot.com-54321fffffabc123':
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
          builds=[
              generate_bb_get_multi_result({'sheriff_rotations': 'chromium'})
          ],
          step_name=('verify_reproducing_step.find_related_builders'
                     '.buildbucket.get_multi'),
      ),
      api.luci_analysis.query_test_history(
          query_test_history_res,
          test_id='ninja://base:base_unittests/MockUnitTests.FailTest',
          parent_step_name='verify_reproducing_step.find_related_builders',
          step_iteration=2,
      ),
      api.buildbucket.simulated_get_multi(
          builds=[generate_bb_get_multi_result({})],
          step_name=('verify_reproducing_step.find_related_builders'
                     '.buildbucket.get_multi (2)'),
      ),
      api.luci_analysis.query_test_history(
          test_history.QueryTestHistoryResponse(),
          test_id='ninja://base:base_unittests/MockUnitTests.FailTest',
          parent_step_name='verify_reproducing_step.find_related_builders',
          step_iteration=3,
      ),
      api.resultdb.query_test_results(
          resultdb_pb2.QueryTestResultsResponse(test_results=[
              dict(
                  test_id='ninja://base:base_unittests/MockUnitTests.FailTest',
                  name=('invalid_result_name'),
                  variant=common_pb2.Variant(
                      **{'def': {
                          'builder': 'Win10 Tests x64',
                      }}),
              ),
              dict(
                  test_id='ninja://base:base_unittests/MockUnitTests.FailTest',
                  name=('invocations/task-example.swarmingserver.appspot.com'
                        '-54321fffffabc001/result-1'),
                  variant=common_pb2.Variant(
                      **{'def': {
                          'builder': 'Win10 Tests x64',
                      }}),
              )
          ]),
          step_name=('verify_reproducing_step.find_related_builders'
                     '.query_test_results'),
      ),
      api.step_data(
          'verify_reproducing_step.get_test_binary from 54321fffffabc123',
          api.json.output_stream(
              api.json.loads(
                  api.flaky_reproducer.get_test_data(
                      'gtest_task_request.json')))),
      api.step_data('verify_reproducing_step.collect verify results',
                    api.swarming.collect([verify_swarming_result('2')])),
      api.step_data(
          'verify_reproducing_step.load verify result',
          api.file.read_json(
              api.json.loads(
                  api.flaky_reproducer.get_test_data(
                      'gtest_good_output.json')))),
      api.step_data(
          'verify_reproducing_step.load verify result (2)',
          api.file.read_json(
              api.json.loads(
                  api.flaky_reproducer.get_test_data(
                      'gtest_good_output.json')))),
      api.step_data(
          'summarize_results.post_summary_to_monorail'
          '.ModifyIssues projects/chromium/issues/123',
          api.json.output_stream(issue_result),
      ),
  )

  yield api.test(
      'no_exists_task_result',
      api.properties(
          task_id='54321fffffabc123', test_name='MockUnitTests.FailTest'),
      api.step_data('get_test_result_summary.swarming collect',
                    api.swarming.collect([])),
      api.expect_status('FAILURE'),
      api.post_process(ResultReason,
                       'Cannot find TaskResult for task 54321fffffabc123.'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'not_supported_task_result',
      api.properties(
          task_id='54321fffffabc123', test_name='MockUnitTests.FailTest'),
      api.step_data('get_test_result_summary.download swarming outputs',
                    api.raw_io.output_dir({})),
      api.expect_status('FAILURE'),
      api.post_process(ResultReason, 'Not supported task result.'),
      api.post_process(DropExpectation),
  )

  success_swarming_result_without_reproducing_step = api.swarming.task_result(
      id='0',
      name='flaky reproducer strategy batch for MockUnitTests.FailTest',
      state=api.swarming.TaskState.COMPLETED,
      output='some-output',
  )
  yield api.test(
      'strategy_without_result',
      api.properties(
          task_id='54321fffffabc123',
          test_name='MockUnitTests.FailTest',
          config='manual'),
      api.step_data(
          'get_test_result_summary.download swarming outputs',
          api.raw_io.output_dir({
              'output.json':
                  api.flaky_reproducer.get_test_data('gtest_good_output.json'),
          })),
      api.step_data(
          'get_test_binary from 54321fffffabc123',
          api.json.output_stream(
              api.json.loads(
                  api.flaky_reproducer.get_test_data(
                      'gtest_task_request.json')))),
      api.step_data(
          'collect strategy results',
          api.swarming.collect(
              [success_swarming_result_without_reproducing_step])),
  )

  yield api.test(
      'test_name_not_in_test_result',
      api.properties(task_id='54321fffffabc123', test_name='NotExists.Test'),
      api.step_data(
          'get_test_result_summary.download swarming outputs',
          api.raw_io.output_dir({
              'output.json':
                  api.flaky_reproducer.get_test_data('gtest_good_output.json'),
          })),
  )

  yield api.test(
      'unknown_result_summary',
      api.properties(task_id='54321fffffabc123', test_name='NotExists.Test'),
      api.step_data('get_test_result_summary.download swarming outputs',
                    api.raw_io.output_dir({'output.json': b'{}'})),
  )

  yield api.test(
      'strategy_failure',
      api.properties(
          task_id='54321fffffabc120',
          test_name='MockUnitTests.FailTest',
          config='manual'),
      api.step_data(
          'get_test_result_summary.download swarming outputs',
          api.raw_io.output_dir({
              'output.json':
                  api.flaky_reproducer.get_test_data('gtest_good_output.json'),
          })),
      api.step_data(
          'get_test_binary from 54321fffffabc121',
          api.json.output_stream(
              api.json.loads(
                  api.flaky_reproducer.get_test_data(
                      'gtest_task_request.json')))),
      api.step_data('collect strategy results',
                    api.swarming.collect(failed_swarming_results)),
      api.expect_status('FAILURE'),
      api.post_process(
          ResultReason, '''Error while running:
* flaky reproducer strategy batch for MockUnitTests.FailTest
* flaky reproducer strategy repeat for MockUnitTests.FailTest'''),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'monorail issue posted',
      api.properties(
          task_id='54321fffffabc123',
          test_name='MockUnitTests.FailTest',
          config='manual',
          monorail_issue='123'),
      api.step_data(
          'check_monorail_comment_posted'
          '.GetIssue projects/chromium/issues/123',
          api.json.output_stream({'labels': [{
              'label': 'flaky-reproduced'
          }]}),
      ),
      api.post_check(StatusSuccess),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'query_sample_failure_from_luci_analysis',
      api.properties(monorail_issue='123'),
      api.step_data(
          'check_monorail_comment_posted'
          '.GetIssue projects/chromium/issues/123',
          api.json.output_stream(issue_result),
      ),
      api.luci_analysis.lookup_bug(
          [],
          'chromium/123',
          parent_step_name='query_sample_failure_from_luci_analysis'),
      api.post_check(ResultReason, 'No cluster associated with bug.'),
      api.post_process(DropExpectation),
  )
