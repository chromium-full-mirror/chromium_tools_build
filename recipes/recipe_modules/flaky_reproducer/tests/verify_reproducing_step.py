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
  resultdb,
  step,
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
  resultdb: resultdb.API
  step: step.API
  swarming: swarming.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  flaky_reproducer: flaky_reproducer.TEST_API
  json: json.TEST_API
  luci_analysis: luci_analysis.TEST_API
  properties: properties.TEST_API
  resultdb: resultdb.TEST_API
  swarming: swarming.TEST_API


PROPERTIES = {
  'task_id': Property(default=None, kind=str),
  'test_name': Property(default=None, kind=str),
  'result_summary_path': Property(default=None, kind=str),
  'reproducing_step_path': Property(default=None, kind=str),
  'verify_on_builders': Property(default=None, kind=list),
  'monorail_issue': Property(default=None, kind=str),
}


def RunSteps(
  api: DEPS,
  task_id,
  test_name,
  result_summary_path,
  reproducing_step_path,
  verify_on_builders,
  monorail_issue,
):
  api.flaky_reproducer.set_config('auto')
  builder_results = api.flaky_reproducer.verify_reproducing_step(
    task_id,
    test_name,
    result_summary_path,
    reproducing_step_path,
    verify_on_builders,
  )
  api.flaky_reproducer.summarize_results(
    task_id,
    test_name,
    reproducing_step_path,
    [] if reproducing_step_path is None else [reproducing_step_path],
    builder_results,
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
            key="test_name", value="MockUnitTests.FailTest"
          ),
        ],
      ),
    ],
  )
  test_running_history = resultdb_pb2.QueryTestResultsResponse(
    test_results=[
      test_result_pb2.TestResult(
        test_id='ninja://base:base_unittests/MockUnitTests.FailTest',
        name=(
          'invocations/task-example.swarmingserver.appspot.com'
          '-54321fffffabc001/result-1'
        ),
        variant=common_pb2.Variant(
          **{
            'def': {
              'builder': 'Linux Tests',
            }
          }
        ),
      ),
      test_result_pb2.TestResult(
        test_id='ninja://base:base_unittests/MockUnitTests.FailTest',
        name='unknown-name-format',
        variant=common_pb2.Variant(
          **{
            'def': {
              'builder': 'Linux Tests',
            }
          }
        ),
      ),
    ]
  )
  query_variants_res = test_history.QueryVariantsResponse(
    variants=[
      test_history.QueryVariantsResponse.VariantInfo(
        variant_hash='dummy_hash_1',
        variant=analysis_common_pb2.Variant(
          **{"def": {'builder': 'Win10 Tests x64'}}
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
        status=test_verdict.TestVerdictStatus.EXPECTED,
      ),
    ]
  )

  def generate_bb_get_multi_result(prop: dict[str, typing.Any]) -> dict:
    props = struct_pb2.Struct()
    props.update(prop)
    return {'id': 1234, 'input': {'properties': props}}

  yield api.test(
    'cannot_retrieve_invocation',
    api.properties(
      task_id='some-task-id',
      test_name='some-test',
      result_summary_path=api.flaky_reproducer.get_test_path(
        'gtest_good_output.json'
      ),
      reproducing_step_path=api.flaky_reproducer.get_test_path(
        'reproducing_step.json'
      ),
    ),
    api.expect_status('FAILURE'),
    api.post_check(
      post_process.SummaryMarkdown,
      'Cannot retrieve invocation for task some-task-id.',
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'cannot_find_test_result_for_test',
    api.properties(
      task_id='some-task-id',
      test_name='Not.Exists.Test',
      result_summary_path=api.flaky_reproducer.get_test_path(
        'gtest_good_output.json'
      ),
      reproducing_step_path=api.flaky_reproducer.get_test_path(
        'reproducing_step.json'
      ),
    ),
    api.resultdb.query(
      {
        'task-example.swarmingserver.appspot.com-some-task-id': resultdb_invocation,
      },
      step_name='verify_reproducing_step.find_related_builders.rdb query',
    ),
    api.expect_status('FAILURE'),
    api.post_check(
      post_process.SummaryMarkdown,
      'Cannot find TestResult for test Not.Exists.Test.',
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'verify_builder_failed',
    api.properties(
      task_id='some-task-id',
      test_name='MockUnitTests.FailTest',
      result_summary_path=api.flaky_reproducer.get_test_path(
        'gtest_good_output.json'
      ),
      reproducing_step_path=api.flaky_reproducer.get_test_path(
        'reproducing_step.json'
      ),
    ),
    api.resultdb.query(
      {
        'task-example.swarmingserver.appspot.com-some-task-id': resultdb_invocation,
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
      builds=[generate_bb_get_multi_result({'$recipe_engine/cq': ''})],
      step_name=(
        'verify_reproducing_step.find_related_builders.buildbucket.get_multi'
      ),
    ),
    api.resultdb.query_test_results(
      test_running_history,
      step_name=(
        'verify_reproducing_step.find_related_builders.query_test_results'
      ),
    ),
    api.step_data(
      'verify_reproducing_step.verify on Linux Tests'
      '.get_test_binary from 54321fffffabc001',
      api.json.output_stream(
        api.json.loads(
          api.flaky_reproducer.get_test_data('gtest_task_request.json')
        )
      ),
    ),
    api.step_data(
      'verify_reproducing_step.verify on failing sample'
      '.get_test_binary from some-task-id',
      api.json.output_stream(
        api.json.loads(
          api.flaky_reproducer.get_test_data('gtest_task_request.json')
        )
      ),
    ),
    api.step_data(
      'verify_reproducing_step.collect verify results',
      api.swarming.collect(
        [
          api.swarming.task_result(
            '0', 'name', failure=True, output='failed-output'
          ),
          api.swarming.task_result('1', 'name', output='failed-output'),
        ]
      ),
    ),
    api.post_check(
      lambda check, steps: check(
        steps['summarize_results'].step_summary_text,
        re.search(
          r"failing sample\s+\[not reproduced",
          steps['summarize_results'].step_summary_text,
        ),
      )
    ),
    api.post_check(
      lambda check, steps: check(
        steps['summarize_results'].step_summary_text,
        re.search(
          r"Linux Tests.+with failure:",
          steps['summarize_results'].step_summary_text,
        ),
      )
    ),
    api.post_process(post_process.DropExpectation),
  )

  verify_swarming_result = api.swarming.task_result(
    id='0',
    name='flaky reproducer verify on Linux Tests for MockUnitTests.FailTest',
    state=api.swarming.TaskState.COMPLETED,
    output='some-output',
    outputs=('result_summary_0.json',),
  )
  gtest_empty_result = api.json.loads(
    api.flaky_reproducer.get_test_data('gtest_good_output.json')
  )
  gtest_empty_result['per_iteration_data'] = []
  yield api.test(
    'verify_not_reproducible',
    api.properties(
      task_id='some-task-id',
      test_name='MockUnitTests.FailTest',
      result_summary_path=api.flaky_reproducer.get_test_path(
        'gtest_good_output.json'
      ),
      reproducing_step_path=api.flaky_reproducer.get_test_path(
        'reproducing_step.json'
      ),
      verify_on_builders=['Win10 Tests x64'],
    ),
    api.resultdb.query(
      {
        'task-example.swarmingserver.appspot.com-some-task-id': resultdb_invocation,
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
      builds=[generate_bb_get_multi_result({'$recipe_engine/cq': ''})],
      step_name=(
        'verify_reproducing_step.find_related_builders.buildbucket.get_multi'
      ),
    ),
    api.resultdb.query_test_results(
      test_running_history,
      step_name=(
        'verify_reproducing_step.find_related_builders.query_test_results'
      ),
    ),
    api.step_data(
      'verify_reproducing_step.verify on Linux Tests'
      '.get_test_binary from 54321fffffabc001',
      api.json.output_stream(
        api.json.loads(
          api.flaky_reproducer.get_test_data('gtest_task_request.json')
        )
      ),
    ),
    api.step_data(
      'verify_reproducing_step.collect verify results',
      api.swarming.collect([verify_swarming_result]),
    ),
    api.post_check(
      lambda check, steps: check(
        steps['summarize_results'].step_summary_text,
        re.search(
          r"not reproduced", steps['summarize_results'].step_summary_text
        ),
      )
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'no_invocations_found_for_related_builders',
    api.properties(
      task_id='some-task-id',
      test_name='MockUnitTests.FailTest',
      result_summary_path=api.flaky_reproducer.get_test_path(
        'gtest_good_output.json'
      ),
      reproducing_step_path=api.flaky_reproducer.get_test_path(
        'reproducing_step.json'
      ),
    ),
    api.resultdb.query(
      {
        'task-example.swarmingserver.appspot.com-some-task-id': resultdb_invocation,
      },
      step_name='verify_reproducing_step.find_related_builders.rdb query',
    ),
    api.luci_analysis.query_variants(
      test_history.QueryVariantsResponse(variants=[]),
      test_id='ninja://base:base_unittests/MockUnitTests.FailTest',
      parent_step_name='verify_reproducing_step.find_related_builders',
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'verify nothing if no verify_on_builders matches',
    api.properties(
      task_id='some-task-id',
      test_name='MockUnitTests.FailTest',
      result_summary_path=api.flaky_reproducer.get_test_path(
        'gtest_good_output.json'
      ),
      reproducing_step_path=api.flaky_reproducer.get_test_path(
        'reproducing_step.json'
      ),
      verify_on_builders=['not_exists_builder'],
    ),
    api.resultdb.query(
      {
        'task-example.swarmingserver.appspot.com-some-task-id': resultdb_invocation,
      },
      step_name='verify_reproducing_step.find_related_builders.rdb query',
    ),
    api.luci_analysis.query_variants(
      query_variants_res,
      test_id='ninja://base:base_unittests/MockUnitTests.FailTest',
      parent_step_name='verify_reproducing_step.find_related_builders',
    ),
    api.post_check(
      lambda check, steps: check(
        'builder_results.json' not in steps['summarize_results'].logs
      )
    ),
    api.post_process(post_process.DropExpectation),
  )
