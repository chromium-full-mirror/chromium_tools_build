# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from PB.go.chromium.org.luci.resultdb.proto.v1 import common as common_pb

from RECIPE_MODULES.build.chromium_tests import steps
from RECIPE_MODULES.build.test_utils import util

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium, chromium_rts, chromium_tests
from RECIPE_MODULES.recipe_engine import (
  file,
  json,
  path,
  platform,
  properties,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_rts: chromium_rts.API
  chromium_tests: chromium_tests.API
  file: file.API
  json: json.API
  path: path.API
  platform: platform.API
  properties: properties.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API
  file: file.TEST_API
  path: path.TEST_API
  properties: properties.TEST_API


def RunSteps(api: DEPS):
  test_suite_name = api.properties.get('test_suite_name', 'MockTest')
  is_orchestrator = api.properties.get('is_orchestrator', False)
  per_suffix_valid = api.properties.get('per_suffix_valid', {})
  per_suffix_failures = api.properties.get('per_suffix_failures', {})
  per_suffix_invalid = api.properties.get('per_suffix_invalid', {})
  per_suffix_complete = api.properties.get('per_suffix_complete', {})
  tests = api.properties.get('tests', ['TestName1', 'TestName2', 'TestName3'])
  has_rdb_results = api.properties.get('has_rdb_results', True)
  enable_rts_filtering = api.properties.get('enable_rts_filtering', False)

  mock_test = steps.MockTestSpec.create(
    test_suite_name,
    has_valid_results=per_suffix_valid.get('with patch', True)
    if has_rdb_results
    else False,
    failures=list(per_suffix_failures.get('with patch', [])),
    per_suffix_valid=per_suffix_valid,
    per_suffix_failures=per_suffix_failures,
    per_suffix_complete=per_suffix_complete,
    invocation_names=['invocations/inv-1'] if has_rdb_results else [],
    enable_rts_filtering=enable_rts_filtering,
  ).get_test(api.chromium_tests)

  second_test_enable_rts = api.properties.get(
    'second_test_enable_rts_filtering', False
  )
  second_test = steps.MockTestSpec.create(
    'SecondTest',
    enable_rts_filtering=second_test_enable_rts,
  ).get_test(api.chromium_tests)

  test_list = [mock_test]
  if api.properties.get('include_second_test', False):
    test_list.append(second_test)

  if api.properties.get('include_derivative_suite', False):
    derivative_suite = steps.MockTestSpec.create(
      'pixel_' + test_suite_name,
      target_name=test_suite_name,
      enable_rts_filtering=False,
    ).get_test(api.chromium_tests)
    test_list.append(derivative_suite)

  if enable_rts_filtering:
    api.chromium_rts._overwritten_tests.add(test_suite_name)
  if second_test_enable_rts:
    api.chromium_rts._overwritten_tests.add('SecondTest')

  if has_rdb_results:
    # Ensure 'with patch' is populated even if there are no failures
    suffixes_to_mock = set(per_suffix_failures.keys())
    suffixes_to_mock.add('with patch')

    for suffix in suffixes_to_mock:
      failed_tests = per_suffix_failures.get(suffix, [])
      individual_tests = []
      unexpected_failing = set()
      individual_unexpected = {}
      for t_name in tests:
        failed = t_name in failed_tests
        t_res = util.RDBPerIndividualTestResults(
          test_name=t_name,
          test_id='%s.%s' % (test_suite_name, t_name),
          invocation_id='inv-1',
          statuses=[
            util.test_result_pb2.FAIL if failed else util.test_result_pb2.PASS
          ],
          expectednesses=[not failed],
          failure_reasons=[],
          test_metadata_file_name='',
        )
        individual_tests.append(t_res)
        if failed:
          unexpected_failing.add(t_res)
          individual_unexpected[t_name] = t_res

      rdb_res = util.RDBPerSuiteResults(
        suite_name=test_suite_name,
        variant=common_pb.Variant(),
        variant_hash='',
        total_tests_ran=len(individual_tests),
        unexpected_passing_tests=set(),
        unexpected_failing_tests=unexpected_failing,
        unexpected_skipped_tests=set(),
        invalid=per_suffix_invalid.get(suffix, False)
        or (per_suffix_valid.get(suffix, True) is False),
        individual_unexpected_test_by_test_name=individual_unexpected,
        all_tests=individual_tests,
        test_id_prefix='',
        exists_unexpected_failing_result=len(unexpected_failing) > 0,
      )
      mock_test.update_rdb_results(suffix, rdb_res)

  build_dir = api.path.cleanup_dir
  if is_orchestrator:
    build_dir = api.path.cleanup_dir / 'out' / 'compilator_build'

  assert api.chromium_rts._evaluation_future is None
  api.chromium_rts.start_evaluation(build_dir, test_list)
  assert api.chromium_rts._evaluation_future is not None

  # Subsequent call should be ignored.
  old_future = api.chromium_rts._evaluation_future
  api.chromium_rts.start_evaluation(build_dir, test_list)
  assert api.chromium_rts._evaluation_future is old_future

  api.chromium_rts.wait_for_evaluation()
  assert api.chromium_rts._evaluation_future is None
  api.chromium_rts.wait_for_evaluation()


def GenTests(api: TEST_DEPS):
  yield api.test(
    'rts_evaluation_basic_test',
    api.chromium.try_build(
      builder='linux-rel', experiments=['chromium_rts.filter_file_analysis']
    ),
    api.properties(per_suffix_failures={'with patch': ['TestName1']}),
    api.path.exists(api.path.cleanup_dir / 'gen' / 'rts' / 'MockTest.filter'),
    api.post_process(post_process.MustRun, 'Evaluate chromium-rts safety'),
    api.post_process(
      post_process.StepTextContains,
      'Evaluate chromium-rts safety',
      [
        'Overall Test Recall: 0.00% (0/1 caught)',
        'Overall Builder Recall: 0.00%',
        'Total Inactive Tests Skipped by RTS: 2',
      ],
    ),
    api.post_process(
      post_process.PropertyEquals,
      'rts_safety_summary',
      {
        'had_unexpected_failures': True,
        'test_recall_rate': 0.0,
        'builder_recall_rate': 0.0,
        'total_rts_skipped_tests': 0,
        'total_rts_inactive_skipped_tests': 2,
      },
    ),
    api.post_process(
      post_process.PropertyEquals,
      'rts_suite_safety_details',
      {
        'MockTest': {
          'test_suite': 'MockTest',
          'rts_skipped_tests_count': 2,
          'unexpected_failures_count': 1,
          'caught_failures_count': 0,
          'missed_failures': ['TestName1'],
          'test_recall_rate': 0.0,
          'rts_banned': False,
        }
      },
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'rts_evaluation_basic_test_caught_failure',
    api.chromium.try_build(
      builder='linux-rel', experiments=['chromium_rts.filter_file_analysis']
    ),
    api.properties(per_suffix_failures={'with patch': ['TestName3']}),
    api.path.exists(api.path.cleanup_dir / 'gen' / 'rts' / 'MockTest.filter'),
    api.post_process(post_process.MustRun, 'Evaluate chromium-rts safety'),
    api.post_process(
      post_process.StepTextContains,
      'Evaluate chromium-rts safety',
      [
        'Overall Test Recall: 100.00% (1/1 caught)',
        'Overall Builder Recall: 100.00%',
        'Total Inactive Tests Skipped by RTS: 2',
      ],
    ),
    api.post_process(
      post_process.PropertyEquals,
      'rts_safety_summary',
      {
        'had_unexpected_failures': True,
        'test_recall_rate': 1.0,
        'builder_recall_rate': 1.0,
        'total_rts_skipped_tests': 0,
        'total_rts_inactive_skipped_tests': 2,
      },
    ),
    api.post_process(
      post_process.PropertiesDoNotContain, 'rts_suite_safety_details'
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'rts_evaluation_active_filtering_suite',
    api.chromium.try_build(
      builder='linux-rel', experiments=['chromium_rts.filter_file_analysis']
    ),
    api.properties(
      enable_rts_filtering=True,
      per_suffix_failures={'with patch': ['TestName1']},
    ),
    api.path.exists(api.path.cleanup_dir / 'gen' / 'rts' / 'MockTest.filter'),
    api.post_process(post_process.MustRun, 'Evaluate chromium-rts safety'),
    api.post_process(
      post_process.StepTextContains,
      'Evaluate chromium-rts safety',
      [
        'Overall Test Recall: 100.00% (0/0 caught)',
        'Overall Builder Recall: 100.00%',
        'Total Tests Skipped by RTS: 2',
      ],
    ),
    api.post_process(
      post_process.PropertyEquals,
      'rts_safety_summary',
      {
        'had_unexpected_failures': False,
        'test_recall_rate': 1.0,
        'builder_recall_rate': 1.0,
        'total_rts_skipped_tests': 2,
        'total_rts_inactive_skipped_tests': 0,
      },
    ),
    api.post_process(
      post_process.PropertyEquals,
      'rts_suite_safety_details',
      {
        'MockTest': {
          'test_suite': 'MockTest',
          'rts_skipped_tests_count': 2,
          'rts_banned': False,
          'actively_filtered': True,
        }
      },
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'rts_evaluation_mixed_active_and_shadow_suites',
    api.chromium.try_build(
      builder='linux-rel', experiments=['chromium_rts.filter_file_analysis']
    ),
    api.properties(
      enable_rts_filtering=True,  # MockTest is active
      second_test_enable_rts_filtering=False,  # SecondTest is shadow
      include_second_test=True,
      per_suffix_failures={'with patch': ['TestName1']},
    ),
    api.path.exists(
      api.path.cleanup_dir / 'gen' / 'rts' / 'MockTest.filter',
      api.path.cleanup_dir / 'gen' / 'rts' / 'SecondTest.filter',
    ),
    api.step_data(
      'Evaluate chromium-rts safety.read MockTest filter file',
      api.file.read_text('-Test1\n-Test2\n-Test3'),
    ),
    api.step_data(
      'Evaluate chromium-rts safety.read SecondTest filter file',
      api.file.read_text('-TestA\n-TestB'),
    ),
    api.post_process(post_process.MustRun, 'Evaluate chromium-rts safety'),
    api.post_process(
      post_process.StepTextContains,
      'Evaluate chromium-rts safety',
      [
        'Overall Test Recall: 100.00% (0/0 caught)',
        'Overall Builder Recall: 100.00%',
        'Total Tests Skipped by RTS: 3',
        'Total Inactive Tests Skipped by RTS: 2',
      ],
    ),
    api.post_process(
      post_process.PropertyEquals,
      'rts_safety_summary',
      {
        'had_unexpected_failures': False,
        'test_recall_rate': 1.0,
        'builder_recall_rate': 1.0,
        'total_rts_skipped_tests': 3,
        'total_rts_inactive_skipped_tests': 2,
      },
    ),
    api.post_process(
      post_process.PropertyEquals,
      'rts_suite_safety_details',
      {
        'MockTest': {
          'test_suite': 'MockTest',
          'rts_skipped_tests_count': 3,
          'rts_banned': False,
          'actively_filtered': True,
        }
      },
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'rts_evaluation_mixed_active_and_shadow_with_failures',
    api.chromium.try_build(
      builder='linux-rel', experiments=['chromium_rts.filter_file_analysis']
    ),
    api.properties(
      enable_rts_filtering=False,  # MockTest is shadow
      second_test_enable_rts_filtering=True,  # SecondTest is active
      include_second_test=True,
      per_suffix_failures={'with patch': ['TestName1']},
    ),
    api.path.exists(
      api.path.cleanup_dir / 'gen' / 'rts' / 'MockTest.filter',
      api.path.cleanup_dir / 'gen' / 'rts' / 'SecondTest.filter',
    ),
    api.step_data(
      'Evaluate chromium-rts safety.read MockTest filter file',
      api.file.read_text('-TestName2\n-TestName3'),
    ),
    api.step_data(
      'Evaluate chromium-rts safety.read SecondTest filter file',
      api.file.read_text('-Test1\n-Test2\n-Test3\n-Test4'),
    ),
    api.post_process(post_process.MustRun, 'Evaluate chromium-rts safety'),
    api.post_process(
      post_process.StepTextContains,
      'Evaluate chromium-rts safety',
      [
        'Overall Test Recall: 100.00% (1/1 caught)',
        'Overall Builder Recall: 100.00%',
        'Total Tests Skipped by RTS: 4',
        'Total Inactive Tests Skipped by RTS: 2',
      ],
    ),
    api.post_process(
      post_process.PropertyEquals,
      'rts_safety_summary',
      {
        'had_unexpected_failures': True,
        'test_recall_rate': 1.0,
        'builder_recall_rate': 1.0,
        'total_rts_skipped_tests': 4,
        'total_rts_inactive_skipped_tests': 2,
      },
    ),
    api.post_process(
      post_process.PropertyEquals,
      'rts_suite_safety_details',
      {
        'SecondTest': {
          'test_suite': 'SecondTest',
          'rts_skipped_tests_count': 4,
          'rts_banned': False,
          'actively_filtered': True,
        }
      },
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'no_failures',
    api.chromium.try_build(
      builder='linux-rel', experiments=['chromium_rts.filter_file_analysis']
    ),
    api.properties(
      per_suffix_failures={'with patch': []},
      tests=['TestName2'],
    ),
    api.path.exists(api.path.cleanup_dir / 'gen' / 'rts' / 'MockTest.filter'),
    api.post_process(post_process.MustRun, 'Evaluate chromium-rts safety'),
    api.post_process(
      post_process.StepTextContains,
      'Evaluate chromium-rts safety',
      [
        'Overall Test Recall: 100.00% (0/0 caught)',
        'Overall Builder Recall: 100.00%',
        'Total Inactive Tests Skipped by RTS: 2',
      ],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'no_rts_support',
    api.chromium.try_build(
      builder='linux-rel', experiments=['chromium_rts.filter_file_analysis']
    ),
    api.properties(
      test_suite_name='NoRtsTest',
      has_rdb_results=False,
      per_suffix_failures={'with patch': ['TestName1']},
      tests=['TestName1'],
    ),
    api.post_process(post_process.MustRun, 'Evaluate chromium-rts safety'),
    api.post_process(
      post_process.StepTextContains,
      'Evaluate chromium-rts safety',
      [
        'Overall Test Recall: 100.00% (0/0 caught)',
        'Overall Builder Recall: 100.00%',
        'Missing RTS filter files for: NoRtsTest',
      ],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'filter_file_missing',
    api.chromium.try_build(
      builder='linux-rel', experiments=['chromium_rts.filter_file_analysis']
    ),
    api.properties(
      per_suffix_failures={'with patch': ['TestName1']},
      tests=['TestName1'],
    ),
    api.post_process(post_process.MustRun, 'Evaluate chromium-rts safety'),
    api.post_process(
      post_process.StepTextContains,
      'Evaluate chromium-rts safety',
      [
        'Overall Test Recall: 100.00% (1/1 caught)',
        'Overall Builder Recall: 100.00%',
        'Missing RTS filter files for: MockTest',
      ],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'missing_rdb_results',
    api.chromium.try_build(
      builder='linux-rel', experiments=['chromium_rts.filter_file_analysis']
    ),
    api.properties(
      per_suffix_failures={},
      has_rdb_results=False,
    ),
    api.path.exists(api.path.cleanup_dir / 'gen' / 'rts' / 'MockTest.filter'),
    api.post_process(post_process.MustRun, 'Evaluate chromium-rts safety'),
    api.post_process(
      post_process.StepTextContains,
      'Evaluate chromium-rts safety',
      [
        'Overall Test Recall: 100.00% (0/0 caught)',
        'Overall Builder Recall: 100.00%',
      ],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'invalid_results',
    api.chromium.try_build(
      builder='linux-rel', experiments=['chromium_rts.filter_file_analysis']
    ),
    api.properties(
      per_suffix_failures={'with patch': ['TestName1']},
      per_suffix_valid={'with patch': False},
      per_suffix_invalid={'with patch': True},
      tests=['TestName1'],
    ),
    api.path.exists(api.path.cleanup_dir / 'gen' / 'rts' / 'MockTest.filter'),
    api.post_process(post_process.MustRun, 'Evaluate chromium-rts safety'),
    api.post_process(
      post_process.StepTextContains,
      'Evaluate chromium-rts safety',
      [
        'Overall Test Recall: 100.00% (0/0 caught)',
        'Overall Builder Recall: 100.00%',
      ],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'orchestrator_path_resolution',
    api.chromium.try_build(
      builder='linux-rel', experiments=['chromium_rts.filter_file_analysis']
    ),
    api.properties(
      per_suffix_failures={'with patch': ['TestName1']},
      is_orchestrator=True,
    ),
    api.path.exists(
      api.path.cleanup_dir
      / 'out'
      / 'compilator_build'
      / 'gen'
      / 'rts'
      / 'MockTest.filter'
    ),
    api.post_process(post_process.MustRun, 'Evaluate chromium-rts safety'),
    api.post_process(
      post_process.StepTextContains,
      'Evaluate chromium-rts safety',
      [
        'Overall Test Recall: 0.00% (0/1 caught)',
        'Overall Builder Recall: 0.00%',
        'Total Inactive Tests Skipped by RTS: 2',
      ],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'evaluation_with_caught_failures',
    api.chromium.try_build(
      builder='linux-rel', experiments=['chromium_rts.filter_file_analysis']
    ),
    api.properties(
      per_suffix_failures={'with patch': ['TestName1']},
    ),
    api.path.exists(api.path.cleanup_dir / 'gen' / 'rts' / 'MockTest.filter'),
    api.step_data(
      'Evaluate chromium-rts safety.read MockTest filter file',
      api.file.read_text('-TestName2'),
    ),
    api.post_process(post_process.MustRun, 'Evaluate chromium-rts safety'),
    api.post_process(
      post_process.StepTextContains,
      'Evaluate chromium-rts safety',
      [
        'Overall Test Recall: 100.00% (1/1 caught)',
        'Overall Builder Recall: 100.00%',
        'Total Inactive Tests Skipped by RTS: 1',
      ],
    ),
    api.post_process(
      post_process.PropertyEquals,
      'rts_safety_summary',
      {
        'had_unexpected_failures': True,
        'test_recall_rate': 1.0,
        'builder_recall_rate': 1.0,
        'total_rts_skipped_tests': 0,
        'total_rts_inactive_skipped_tests': 1,
      },
    ),
    api.post_process(
      post_process.PropertiesDoNotContain, 'rts_suite_safety_details'
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'evaluation_internal_error',
    api.chromium.try_build(
      builder='linux-rel', experiments=['chromium_rts.filter_file_analysis']
    ),
    api.properties(
      per_suffix_failures={'with patch': ['TestName1']},
    ),
    api.path.exists(api.path.cleanup_dir / 'gen' / 'rts' / 'MockTest.filter'),
    api.step_data(
      'Evaluate chromium-rts safety.read MockTest filter file', retcode=1
    ),
    api.post_process(post_process.MustRun, 'Evaluate chromium-rts safety'),
    api.post_process(
      post_process.PropertyEquals, 'rts_evaluation_status', 'ERROR'
    ),
    api.post_process(
      post_process.StepTextContains,
      'Evaluate chromium-rts safety',
      [
        'retcode: 1',
      ],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'without_patch_invalid_fallback',
    api.chromium.try_build(
      builder='linux-rel', experiments=['chromium_rts.filter_file_analysis']
    ),
    api.properties(
      per_suffix_failures={
        'with patch': ['TestName1'],
        'without patch': ['TestName1'],
      },
      per_suffix_valid={
        'with patch': True,
        'without patch': False,
      },
      per_suffix_invalid={
        'without patch': True,
      },
      tests=['TestName1'],
    ),
    api.path.exists(api.path.cleanup_dir / 'gen' / 'rts' / 'MockTest.filter'),
    api.post_process(
      post_process.PropertyEquals, 'rts_evaluation_status', 'SUCCESS'
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'evaluation_flaky_retry_pass',
    api.chromium.try_build(
      builder='linux-rel', experiments=['chromium_rts.filter_file_analysis']
    ),
    api.properties(
      per_suffix_failures={
        'with patch': ['TestName1'],
        'retry shards with patch': [],
      },
    ),
    api.path.exists(api.path.cleanup_dir / 'gen' / 'rts' / 'MockTest.filter'),
    api.post_process(post_process.MustRun, 'Evaluate chromium-rts safety'),
    api.post_process(
      post_process.PropertyEquals, 'rts_evaluation_status', 'SUCCESS'
    ),
    api.post_process(
      post_process.StepTextContains,
      'Evaluate chromium-rts safety',
      [
        'Overall Test Recall: 100.00% (0/0 caught)',
      ],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'evaluation_deterministic_failure',
    api.chromium.try_build(
      builder='linux-rel', experiments=['chromium_rts.filter_file_analysis']
    ),
    api.properties(
      per_suffix_failures={
        'with patch': ['TestName1'],
        'retry shards with patch': ['TestName1'],
        'without patch': [],
      },
    ),
    api.path.exists(api.path.cleanup_dir / 'gen' / 'rts' / 'MockTest.filter'),
    api.post_process(post_process.MustRun, 'Evaluate chromium-rts safety'),
    api.post_process(
      post_process.PropertyEquals, 'rts_evaluation_status', 'SUCCESS'
    ),
    api.post_process(
      post_process.StepTextContains,
      'Evaluate chromium-rts safety',
      [
        'Overall Test Recall: 0.00% (0/1 caught)',
      ],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'evaluation_exonerated_by_without_patch',
    api.chromium.try_build(
      builder='linux-rel', experiments=['chromium_rts.filter_file_analysis']
    ),
    api.properties(
      per_suffix_failures={
        'with patch': ['TestName1'],
        'retry shards with patch': ['TestName1'],
        'without patch': ['TestName1'],
      },
    ),
    api.path.exists(api.path.cleanup_dir / 'gen' / 'rts' / 'MockTest.filter'),
    api.post_process(post_process.MustRun, 'Evaluate chromium-rts safety'),
    api.post_process(
      post_process.PropertyEquals, 'rts_evaluation_status', 'SUCCESS'
    ),
    api.post_process(
      post_process.StepTextContains,
      'Evaluate chromium-rts safety',
      [
        'Overall Test Recall: 100.00% (0/0 caught)',
      ],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'evaluation_with_patch_infra_failure',
    api.chromium.try_build(
      builder='linux-rel', experiments=['chromium_rts.filter_file_analysis']
    ),
    api.properties(
      per_suffix_failures={'with patch': ['TestName1']},
      per_suffix_valid={'with patch': False},
      per_suffix_invalid={'with patch': True},
      tests=['TestName1'],
    ),
    api.path.exists(api.path.cleanup_dir / 'gen' / 'rts' / 'MockTest.filter'),
    api.post_process(post_process.MustRun, 'Evaluate chromium-rts safety'),
    api.post_process(
      post_process.PropertyEquals, 'rts_evaluation_status', 'SUCCESS'
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'evaluation_incomplete_suite',
    api.chromium.try_build(
      builder='linux-rel', experiments=['chromium_rts.filter_file_analysis']
    ),
    api.properties(
      per_suffix_failures={'with patch': []},
      per_suffix_complete={'with patch': False},
      tests=['TestName1'],
    ),
    api.path.exists(api.path.cleanup_dir / 'gen' / 'rts' / 'MockTest.filter'),
    api.post_process(post_process.MustRun, 'Evaluate chromium-rts safety'),
    api.post_process(
      post_process.PropertyEquals, 'rts_evaluation_status', 'SKIPPED'
    ),
    api.post_process(
      post_process.PropertiesDoNotContain, 'rts_suite_safety_details'
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'banned_suite_evaluated',
    api.chromium.try_build(
      builder='linux-rel', experiments=['chromium_rts.filter_file_analysis']
    ),
    api.properties(
      test_suite_name='blink_python_tests',
      per_suffix_failures={'with patch': ['TestName1']},
      tests=['TestName1'],
    ),
    api.path.exists(
      api.path.cleanup_dir / 'gen' / 'rts' / 'blink_python_tests.filter'
    ),
    api.post_process(post_process.MustRun, 'Evaluate chromium-rts safety'),
    api.post_process(
      post_process.StepTextContains,
      'Evaluate chromium-rts safety',
      [
        'Overall Test Recall: 0.00% (0/1 caught)',
        'Overall Builder Recall: 0.00%',
      ],
    ),
    api.post_process(
      post_process.PropertyEquals,
      'rts_suite_safety_details',
      {
        'blink_python_tests': {
          'test_suite': 'blink_python_tests',
          'rts_skipped_tests_count': 2,
          'unexpected_failures_count': 1,
          'caught_failures_count': 0,
          'missed_failures': ['TestName1'],
          'test_recall_rate': 0.0,
          'rts_banned': True,
        }
      },
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'evaluation_with_missing_filter_files',
    api.chromium.try_build(
      builder='linux-rel', experiments=['chromium_rts.filter_file_analysis']
    ),
    api.properties(
      per_suffix_failures={'with patch': ['TestName3']},
      include_second_test=True,
    ),
    api.path.exists(api.path.cleanup_dir / 'gen' / 'rts' / 'MockTest.filter'),
    api.post_process(post_process.MustRun, 'Evaluate chromium-rts safety'),
    api.post_process(
      post_process.StepTextContains,
      'Evaluate chromium-rts safety',
      [
        'Overall Test Recall: 100.00% (1/1 caught)',
        'Overall Builder Recall: 100.00%',
        'Total Inactive Tests Skipped by RTS: 2',
        'Missing RTS filter files for: SecondTest',
      ],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'evaluation_derivative_suite_not_actively_filtered',
    api.chromium.try_build(
      builder='win-rel', experiments=['chromium_rts.filter_file_analysis']
    ),
    api.properties(
      test_suite_name='interactive_ui_tests',
      enable_rts_filtering=True,
      include_derivative_suite=True,
    ),
    api.path.exists(
      api.path.cleanup_dir / 'gen' / 'rts' / 'interactive_ui_tests.filter'
    ),
    api.post_process(post_process.MustRun, 'Evaluate chromium-rts safety'),
    api.post_process(
      post_process.StepTextEquals,
      'Evaluate chromium-rts safety',
      '<br/>'.join(
        [
          'RTS Evaluation Summary',
          'Overall Test Recall: 100.00% (0/0 caught)',
          'Overall Builder Recall: 100.00%',
          'Total Tests Skipped by RTS: 2',
        ]
      ),
    ),
    api.post_process(
      post_process.PropertyEquals,
      'rts_suite_safety_details',
      {
        'interactive_ui_tests': {
          'test_suite': 'interactive_ui_tests',
          'rts_skipped_tests_count': 2,
          'rts_banned': False,
          'actively_filtered': True,
        }
      },
    ),
    api.post_process(post_process.DropExpectation),
  )
