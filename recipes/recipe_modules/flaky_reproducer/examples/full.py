# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import typing

from recipe_engine.recipe_api import Property

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import flaky_reproducer
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  file,
  json,
  luci_analysis,
  properties,
  raw_io,
  resultdb,
  swarming,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  file: file.API
  flaky_reproducer: flaky_reproducer.API
  json: json.API
  luci_analysis: luci_analysis.API
  properties: properties.API
  raw_io: raw_io.API
  resultdb: resultdb.API
  swarming: swarming.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  flaky_reproducer: flaky_reproducer.TEST_API
  json: json.TEST_API
  luci_analysis: luci_analysis.TEST_API
  properties: properties.TEST_API
  raw_io: raw_io.TEST_API
  resultdb: resultdb.TEST_API
  swarming: swarming.TEST_API


PROPERTIES = {
  'task_id': Property(default=None, kind=str),
  'build_id': Property(default=None, kind=int),
  'test_name': Property(default=None, kind=str),
  'test_id': Property(default=None, kind=str),
  'config': Property(default=None, kind=str),
  'monorail_issue': Property(default=None, kind=str),
}


def RunSteps(
  api: DEPS, config, task_id, build_id, test_name, test_id, monorail_issue
):
  api.flaky_reproducer.set_config(config)
  return api.flaky_reproducer.run(
    task_id=task_id,
    build_id=build_id,
    test_name=test_name,
    test_id=test_id,
    monorail_issue=monorail_issue,
  )


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


def GenTests(api: TEST_DEPS):
  success_swarming_results = [
    api.swarming.task_result(
      id='0',
      name='flaky reproducer strategy repeat for MockUnitTests.FailTest',
      state=api.swarming.TaskState.COMPLETED,
      output='some-output',
      outputs=('reproducing_step.json',),
    ),
    api.swarming.task_result(
      id='1',
      name='flaky reproducer strategy batch for MockUnitTests.FailTest',
      state=api.swarming.TaskState.COMPLETED,
      output='some-output',
      outputs=(),
    ),
  ]
  failed_swarming_results = [
    api.swarming.task_result(
      id='0',
      name='flaky reproducer strategy repeat for MockUnitTests.FailTest',
      state=api.swarming.TaskState.TIMED_OUT,
      output='some-output',
      outputs=('reproducing_step.json',),
    ),
    api.swarming.task_result(
      id='1',
      name='flaky reproducer strategy batch for MockUnitTests.FailTest',
      state=api.swarming.TaskState.COMPLETED,
      failure=True,
      output='some-output',
      outputs=(),
    ),
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
          }
        ),
        tags=[
          common_pb2.StringPair(
            key="test_name", value="MockUnitTests.FailTest"
          ),
        ],
      ),
    ],
  )
  query_variants_res = test_history.QueryVariantsResponse(
    variants=[
      test_history.QueryVariantsResponse.VariantInfo(
        variant_hash='dummy_hash_1',
        variant=analysis_common_pb2.Variant(
          **{"def": {'builder': 'Win10 Tests x64'}}
        ),
      ),
      test_history.QueryVariantsResponse.VariantInfo(
        variant_hash='dummy_hash_2',
        variant=analysis_common_pb2.Variant(
          **{"def": {'builder': 'Mac11 Tests'}}
        ),
      ),
      test_history.QueryVariantsResponse.VariantInfo(
        variant_hash='dummy_hash_3',
        variant=analysis_common_pb2.Variant(
          **{"def": {'builder': 'Linux Tests'}}
        ),
      ),
      test_history.QueryVariantsResponse.VariantInfo(
        variant_hash='dummy_hash_4',
        variant=analysis_common_pb2.Variant(
          **{"def": {'builder': 'Not Supported Builder'}}
        ),
      ),
    ]
  )
  query_test_history_res = test_history.QueryTestHistoryResponse(
    verdicts=[
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
    ]
  )

  def generate_bb_get_multi_result(prop: dict[str, typing.Any]) -> dict:
    props = struct_pb2.Struct()
    props.update(prop)
    return {'id': 1234, 'input': {'properties': props}}

  verify_swarming_result = lambda id: api.swarming.task_result(
    id=id,
    name='flaky reproducer verify on Linux Tests for MockUnitTests.FailTest',
    state=api.swarming.TaskState.COMPLETED,
    output='some-output',
    outputs=('result_summary_0.json', 'result_summary_1.json'),
  )
  issue_result = {
    "name": "projects/chromium/issues/123",
    "labels": [
      {"derivation": "EXPLICIT", "label": "Restrict-View-Google"},
      {"derivation": "EXPLICIT", "label": "CV-M1"},
    ],
  }
  yield api.test(
    'happy_path',
    api.properties(
      task_id='54321fffffabc123',
      test_name='MockUnitTests.FailTest',
      config='manual',
      monorail_issue='123',
    ),
    api.step_data(
      'check_monorail_comment_posted.GetIssue projects/chromium/issues/123',
      api.json.output_stream(issue_result),
    ),
    api.step_data(
      'get_test_result_summary.download swarming outputs',
      api.raw_io.output_dir(
        {
          'output.json': api.flaky_reproducer.get_test_data(
            'gtest_good_output.json'
          ),
        }
      ),
    ),
    api.step_data(
      'get_test_binary from 54321fffffabc123',
      api.json.output_stream(
        api.json.loads(
          api.flaky_reproducer.get_test_data('gtest_task_request.json')
        )
      ),
    ),
    api.step_data(
      'choose_strategies.api_runner',
      api.json.output_stream(['repeat', 'batch']),
    ),
    api.step_data(
      'collect strategy results', api.swarming.collect(success_swarming_results)
    ),
    api.step_data(
      'choose_best_reproducing_step.api_runner',
      api.json.output_stream({'a': 'b'}),
    ),
    api.resultdb.query(
      {
        'task-example.swarmingserver.appspot.com-54321fffffabc123': resultdb_invocation,
      },
      step_name='verify_reproducing_step.find_related_builders.rdb query',
    ),
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
      builds=[generate_bb_get_multi_result({'sheriff_rotations': 'chromium'})],
      step_name=(
        'verify_reproducing_step.find_related_builders.buildbucket.get_multi'
      ),
    ),
    api.luci_analysis.query_test_history(
      query_test_history_res,
      test_id='ninja://base:base_unittests/MockUnitTests.FailTest',
      parent_step_name='verify_reproducing_step.find_related_builders',
      step_iteration=2,
    ),
    api.buildbucket.simulated_get_multi(
      builds=[generate_bb_get_multi_result({})],
      step_name=(
        'verify_reproducing_step.find_related_builders'
        '.buildbucket.get_multi (2)'
      ),
    ),
    api.luci_analysis.query_test_history(
      test_history.QueryTestHistoryResponse(),
      test_id='ninja://base:base_unittests/MockUnitTests.FailTest',
      parent_step_name='verify_reproducing_step.find_related_builders',
      step_iteration=3,
    ),
    api.resultdb.query_test_results(
      resultdb_pb2.QueryTestResultsResponse(
        test_results=[
          dict(
            test_id='ninja://base:base_unittests/MockUnitTests.FailTest',
            name=('invalid_result_name'),
            variant=common_pb2.Variant(
              **{
                'def': {
                  'builder': 'Win10 Tests x64',
                }
              }
            ),
          ),
          dict(
            test_id='ninja://base:base_unittests/MockUnitTests.FailTest',
            name=(
              'invocations/task-example.swarmingserver.appspot.com'
              '-54321fffffabc001/result-1'
            ),
            variant=common_pb2.Variant(
              **{
                'def': {
                  'builder': 'Win10 Tests x64',
                }
              }
            ),
          ),
        ]
      ),
      step_name=(
        'verify_reproducing_step.find_related_builders.query_test_results'
      ),
    ),
    api.step_data(
      'verify_reproducing_step.verify on failing sample'
      '.get_test_binary from 54321fffffabc123',
      api.json.output_stream(
        api.json.loads(
          api.flaky_reproducer.get_test_data('gtest_task_request.json')
        )
      ),
    ),
    api.step_data(
      'verify_reproducing_step.collect verify results',
      api.swarming.collect([verify_swarming_result('2')]),
    ),
    api.step_data(
      'verify_reproducing_step.count_reproduced_failures.api_runner',
      api.json.output_stream(2),
    ),
    api.step_data(
      'summarize_results.summarize_reproducing_steps.api_runner',
      api.json.output_stream(['<header>', '<reproducing_steps>']),
    ),
    api.step_data(
      'summarize_results.post_summary_to_monorail'
      '.ModifyIssues projects/chromium/issues/123',
      api.json.output_stream(issue_result),
    ),
    api.expect_status('SUCCESS'),
    api.post_check(
      lambda check, steps: check(
        steps['summarize_results'].step_summary_text,
        (
          "<header>  \n"
          "For MockUnitTests.FailTest\n"
          "https://luci-milo.appspot.com/ui/inv/task-example.swarmingserver.appspot.com-54321fffffabc123/test-results?q=MockUnitTests.FailTest\n\n"
          "<reproducing_steps>\n\n"
          "The failure could be reproduced on following builders:\n"
          "failing sample                 [reproduced](https://example.swarmingserver.appspot.com/task?id=2) 2/2  \n"
          "Win10 Tests x64                [not reproduced](https://example.swarmingserver.appspot.com/task?id=None), with failure: 'NoneType' object has no attribute 'get'"
        )
        in steps['summarize_results'].step_summary_text,
      )
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'no_exists_task_result',
    api.properties(
      task_id='54321fffffabc123', test_name='MockUnitTests.FailTest'
    ),
    api.step_data(
      'get_test_result_summary.swarming collect', api.swarming.collect([])
    ),
    api.expect_status('FAILURE'),
    api.post_process(
      post_process.SummaryMarkdown,
      'Cannot find TaskResult for task 54321fffffabc123.',
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'not_supported_task_result',
    api.properties(
      task_id='54321fffffabc123', test_name='MockUnitTests.FailTest'
    ),
    api.step_data(
      'get_test_result_summary.download swarming outputs',
      api.raw_io.output_dir({}),
    ),
    api.expect_status('FAILURE'),
    api.post_process(
      post_process.SummaryMarkdown, 'Not supported task result.'
    ),
    api.post_process(post_process.DropExpectation),
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
      config='manual',
    ),
    api.step_data(
      'get_test_result_summary.download swarming outputs',
      api.raw_io.output_dir(
        {
          'output.json': api.flaky_reproducer.get_test_data(
            'gtest_good_output.json'
          ),
        }
      ),
    ),
    api.step_data(
      'get_test_binary from 54321fffffabc123',
      api.json.output_stream(
        api.json.loads(
          api.flaky_reproducer.get_test_data('gtest_task_request.json')
        )
      ),
    ),
    api.step_data(
      'collect strategy results',
      api.swarming.collect([success_swarming_result_without_reproducing_step]),
    ),
    api.post_process(
      post_process.DoesNotRun, 'choose_best_reproducing_step.api_runner'
    ),
    api.expect_status('SUCCESS'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'strategy_failure',
    api.properties(
      task_id='54321fffffabc120',
      test_name='MockUnitTests.FailTest',
      config='manual',
    ),
    api.step_data(
      'get_test_result_summary.download swarming outputs',
      api.raw_io.output_dir(
        {
          'output.json': api.flaky_reproducer.get_test_data(
            'gtest_good_output.json'
          ),
        }
      ),
    ),
    api.step_data(
      'get_test_binary from 54321fffffabc121',
      api.json.output_stream(
        api.json.loads(
          api.flaky_reproducer.get_test_data('gtest_task_request.json')
        )
      ),
    ),
    api.step_data(
      'collect strategy results', api.swarming.collect(failed_swarming_results)
    ),
    api.expect_status('FAILURE'),
    api.post_process(
      post_process.SummaryMarkdown,
      '''Error while running:
* flaky reproducer strategy batch for MockUnitTests.FailTest
* flaky reproducer strategy repeat for MockUnitTests.FailTest''',
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'monorail issue posted',
    api.properties(
      task_id='54321fffffabc123',
      test_name='MockUnitTests.FailTest',
      config='manual',
      monorail_issue='123',
    ),
    api.step_data(
      'check_monorail_comment_posted.GetIssue projects/chromium/issues/123',
      api.json.output_stream({'labels': [{'label': 'flaky-reproduced'}]}),
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'query_sample_failure_from_luci_analysis',
    api.properties(monorail_issue='123'),
    api.step_data(
      'check_monorail_comment_posted.GetIssue projects/chromium/issues/123',
      api.json.output_stream(issue_result),
    ),
    api.luci_analysis.lookup_bug(
      [],
      'chromium/123',
      parent_step_name='query_sample_failure_from_luci_analysis',
    ),
    api.expect_status('FAILURE'),
    api.post_check(
      post_process.SummaryMarkdown, 'No cluster associated with bug.'
    ),
    api.post_process(post_process.DropExpectation),
  )
