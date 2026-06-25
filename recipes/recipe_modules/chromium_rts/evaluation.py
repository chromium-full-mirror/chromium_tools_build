# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from typing import TypedDict

from recipe_engine import recipe_api
from recipe_engine.config_types import Path
from recipe_engine.engine_types import StepPresentation

from RECIPE_MODULES.build.chromium_tests import steps
from RECIPE_MODULES.build.test_utils import util


class SuiteSafetyDetails(TypedDict):
  test_suite: str
  rts_skipped_tests_count: int
  unexpected_failures_count: int
  caught_failures_count: int
  missed_failures: list[str]
  test_recall_rate: float
  rts_banned: bool


def evaluate_rts(
    api: recipe_api.RecipeApi,
    build_dir: Path,
    tests: list[steps.Test],
    banned_suites: set[str],
) -> None:
  """RTS safety evaluation logic.

  Evaluates the model's safety performance (test and builder recall)
  by comparing filter files and RDB results.

  Args:
    api: The RecipeApi instance.
    build_dir: Path to the root build directory containing generated RTS filter
      files.
    tests: A list of Test objects to evaluate against their ResultDB test
      results.
  """
  step_name = 'Evaluate chromium-rts safety'

  with api.m.step.nest(step_name) as presentation:
    try:
      evaluation_results, missing_filter_suites = _evaluate_all_tests(
          api, build_dir, tests, banned_suites)
      if not evaluation_results:
        step_text = 'No RTS targets had generated filter files or test results.'
        if missing_filter_suites:
          step_text += ('\n\nMissing RTS filter files for: %s' %
                        ', '.join(sorted(missing_filter_suites)))
        presentation.step_text = step_text
        presentation.properties['rts_evaluation_status'] = 'SKIPPED'
        return

      summary_data = _calculate_overall_summary(evaluation_results)
      _present_evaluation_results(api, presentation, evaluation_results,
                                  summary_data, missing_filter_suites)
      presentation.properties['rts_evaluation_status'] = 'SUCCESS'
    except Exception as e:
      presentation.step_text = (
          f'RTS safety evaluation encountered internal error: {e}')
      presentation.properties['rts_evaluation_status'] = 'ERROR'
      presentation.properties['rts_evaluation_error'] = str(e)


def _evaluate_all_tests(
    api: recipe_api.RecipeApi,
    build_dir: Path,
    tests: list[steps.Test],
    banned_suites: set[str],
) -> tuple[dict[str, SuiteSafetyDetails], list[str]]:
  """Evaluates RTS performance across all given tests."""
  filter_file_dir = api.filter_file_dir(build_dir)

  evaluation_results: dict[str, SuiteSafetyDetails] = {}
  missing_filter_suites = []
  for test in tests:
    target_name = test.isolate_target or test.target_name
    filter_file_path = filter_file_dir / f'{target_name}.filter'
    if not api.m.path.exists(filter_file_path):
      missing_filter_suites.append(test.name)
      continue
    skipped_tests = _get_skipped_tests(api, target_name, filter_file_path)
    is_banned = target_name in banned_suites
    res = _evaluate_test_suite(test, skipped_tests, is_banned)
    if res:
      evaluation_results[test.name] = res
  return evaluation_results, missing_filter_suites


def _get_skipped_tests(
    api: recipe_api.RecipeApi,
    target_name: str,
    filter_file_path: Path,
) -> set[str]:
  """Reads the skipped tests from the filter file."""
  filter_content = api.m.file.read_text(
      'read %s filter file' % target_name,
      filter_file_path,
      test_data='-TestName1\n-TestName2')
  skipped_tests = set()
  for line in filter_content.splitlines():
    line = line.strip()
    if line.startswith('-'):
      skipped_tests.add(line[1:])
  return skipped_tests


def _evaluate_test_suite(
    test: steps.Test,
    skipped_tests: set[str],
    is_banned: bool,
) -> SuiteSafetyDetails | None:
  """Evaluates RTS performance for a single test."""
  valid, actual_failures = test.deterministic_without_patch_failures()
  if not valid:
    valid, actual_failures = test.with_patch_failures_including_retry()

  if not valid or actual_failures is None:
    return None

  target_actual_failures = len(actual_failures)
  caught_failures_list = [
      name for name in actual_failures if name not in skipped_tests
  ]
  missed_failures_list = [
      name for name in actual_failures if name in skipped_tests
  ]
  target_caught_failures = len(caught_failures_list)

  target_test_recall = (
      float(target_caught_failures) /
      target_actual_failures if target_actual_failures > 0 else 1.0)

  return {
      'test_suite': test.name,
      'rts_skipped_tests_count': len(skipped_tests),
      'unexpected_failures_count': target_actual_failures,
      'caught_failures_count': target_caught_failures,
      'missed_failures': missed_failures_list[:50],
      'test_recall_rate': target_test_recall,
      'rts_banned': is_banned,
  }


def _calculate_overall_summary(
    evaluation_results: dict[str, SuiteSafetyDetails],
) -> dict[str, bool | float | int]:
  """Calculates overall RTS evaluation summary."""
  results_list = evaluation_results.values()
  actual_failures = sum(
      res['unexpected_failures_count'] for res in results_list)
  caught_failures = sum(res['caught_failures_count'] for res in results_list)
  total_skipped = sum(res['rts_skipped_tests_count'] for res in results_list)

  has_failures = actual_failures > 0

  summary_data: dict[str, bool | float | int] = {
      'had_unexpected_failures': has_failures,
      'total_rts_skipped_tests': total_skipped,
  }

  if has_failures:
    test_recall = float(caught_failures) / actual_failures
    builder_recall = 1.0 if caught_failures > 0 else 0.0
    summary_data['test_recall_rate'] = test_recall
    summary_data['builder_recall_rate'] = builder_recall
  else:
    summary_data['test_recall_rate'] = 1.0
    summary_data['builder_recall_rate'] = 1.0

  return summary_data


def _present_evaluation_results(
    api: recipe_api.RecipeApi,
    presentation: StepPresentation,
    evaluation_results: dict[str, SuiteSafetyDetails],
    summary_data: dict[str, bool | float | int],
    missing_filter_suites: list[str],
) -> None:
  """Presents RTS evaluation results as a step and output properties."""
  total_skipped = summary_data['total_rts_skipped_tests']
  results_list = evaluation_results.values()

  if summary_data['had_unexpected_failures']:
    test_recall_pct = summary_data['test_recall_rate'] * 100
    builder_recall_pct = summary_data['builder_recall_rate'] * 100
    actual_failures = sum(
        res['unexpected_failures_count'] for res in results_list)
    caught_failures = sum(res['caught_failures_count'] for res in results_list)
    summary_lines = [
        '### RTS Evaluation Summary',
        'Overall Test Recall: %.2f%% (%d/%d caught)' %
        (test_recall_pct, caught_failures, actual_failures),
        'Overall Builder Recall: %.2f%%' % builder_recall_pct,
    ]
  else:
    summary_lines = [
        '### RTS Evaluation Summary',
        'Overall Test Recall: 100.00% (0/0 caught)',
        'Overall Builder Recall: 100.00%',
    ]

  if total_skipped > 0:
    summary_lines.append('Total Tests Skipped by RTS: %d' % total_skipped)

  if missing_filter_suites:
    summary_lines.append('')
    summary_lines.append('Missing RTS filter files for: %s' %
                         ', '.join(sorted(missing_filter_suites)))

  presentation.step_text = '\n'.join(summary_lines)
  presentation.properties['rts_safety_summary'] = summary_data
  presentation.properties['rts_suite_safety_details'] = evaluation_results
  presentation.logs['rts_suite_safety_details'] = api.m.json.dumps(
      evaluation_results, indent=2).splitlines()
