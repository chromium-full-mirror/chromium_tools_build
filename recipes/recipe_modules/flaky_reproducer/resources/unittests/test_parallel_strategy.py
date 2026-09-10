# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from libs.test_binary import create_test_binary_from_jsonish, BaseTestBinary
from libs.test_binary.gtest_test_binary import GTestTestBinary
from libs.result_summary import create_result_summary_from_output_json
from libs.result_summary.gtest_result_summary import GTestTestResult
from libs.strategies.parallel_strategy import ParallelStrategy
from testdata import get_test_data
from unittests import GenerateResultSummaryMixin


class ParallelStrategyTest(unittest.TestCase, GenerateResultSummaryMixin):
  def setUp(self):
    self.test_binary = create_test_binary_from_jsonish(
      json.loads(get_test_data('gtest_test_binary.json'))
    )
    self.result_summary = create_result_summary_from_output_json(
      json.loads(get_test_data('gtest_good_output.json'))
    )
    for i, test in enumerate(self.result_summary):
      if test.thread_id is not None:
        test.thread_id += i
        test.duration = 10

  def get_strategy(self, test_name, test_binary=None, result_summary=None):
    test_binary = test_binary or self.test_binary
    result_summary = result_summary or self.result_summary
    return ParallelStrategy(test_binary, result_summary, test_name)

  def test_valid_for_test(self):
    self.assertFalse(
      self.get_strategy('MockUnitTests.AnyTest').valid_for_test()
    )
    self.assertFalse(
      self.get_strategy('MockUnitTests.PassTest').valid_for_test()
    )
    self.assertFalse(
      self.get_strategy(
        'MockUnitTests.CrashTest', test_binary=BaseTestBinary([])
      ).valid_for_test()
    )
    self.assertTrue(
      self.get_strategy('MockUnitTests.CrashTest').valid_for_test()
    )

    for test in self.result_summary:
      test.thread_id = 21
    self.assertFalse(
      self.get_strategy('MockUnitTests.CrashTest').valid_for_test()
    )

  def test_timeout(self):
    strategy = self.get_strategy('MockUnitTests.CrashTest')
    reproducing_step = strategy.run(timeout=0)
    self.assertFalse(reproducing_step)

  def test_no_failing_sample(self):
    with self.assertRaisesRegex(
      Exception, 'Parallel strategy requires failing sample with timing info'
    ):
      self.get_strategy('Not.Exists.Test').run()

  def test_no_parallel_tests(self):
    for test in self.result_summary:
      test.thread_id = 21
    strategy = self.get_strategy('MockUnitTests.CrashTest')
    reproducing_step = strategy.run()
    self.assertFalse(reproducing_step)

  def test_get_parallel_tests(self):
    failing_sample = self.result_summary.get_failing_sample(
      'MockUnitTests.CrashTest'
    )
    self.result_summary.add(
      GTestTestResult(
        'TestResult.WithSameThreadId', thread_id=failing_sample.thread_id
      )
    )
    self.result_summary.add(
      GTestTestResult(
        'TestResult.WithOutDuration',
        thread_id=failing_sample.thread_id + 10,
        start_time=failing_sample.start_time,
        duration=None,
      )
    )
    self.result_summary.add(
      GTestTestResult(
        'TestResult.BeforeSample',
        thread_id=failing_sample.thread_id + 10,
        start_time=failing_sample.start_time - 10,
        duration=99,
      )
    )
    self.result_summary.add(
      GTestTestResult(
        'TestResult.AfterSample',
        thread_id=failing_sample.thread_id + 10,
        start_time=failing_sample.start_time + 10,
        duration=99,
      )
    )
    self.result_summary.add(
      GTestTestResult(
        'TestResult.GoodSample',
        thread_id=failing_sample.thread_id + 10,
        start_time=failing_sample.start_time,
        duration=99,
      )
    )
    strategy = self.get_strategy('MockUnitTests.CrashTest')
    strategy.valid_for_test()
    tests = strategy._get_parallel_tests()
    self.assertListEqual(
      [x.test_name for x in tests],
      [
        'MockUnitTests.FailTest',
        'MockUnitTests.PassTest',
        'TestResult.GoodSample',
      ],
    )

  @patch.object(GTestTestBinary, 'run', autospec=True)
  def test_reproduce_group_step(self, mock_test_binary_run):
    test_self = self

    def mock_test_binary_run_side_effect(self, *args, **kwargs):
      if len(self.tests) == 3:
        return test_self.generate_result_summary(
          'MockUnitTests.CrashTest',
          'C' + 'P' * (self.repeat - 1),
          duration=0,
        )
      return test_self.generate_result_summary(
        'MockUnitTests.CrashTest', 'P' * self.repeat, duration=0
      )

    mock_test_binary_run.side_effect = mock_test_binary_run_side_effect

    # Strategy should get all test as parallel during the failing test running
    # period.
    for test in self.result_summary:
      test.thread_id = None

    strategy = self.get_strategy('MockUnitTests.CrashTest')
    reproducing_step = strategy.run()
    # reproduced 3 times in group and 2 * MAX_ITERATIONS in single test
    self.assertEqual(mock_test_binary_run.call_count, 23)
    self.assertTrue(reproducing_step)
    self.assertListEqual(
      reproducing_step.test_binary.tests,
      [
        'MockUnitTests.FailTest',
        'MockUnitTests.PassTest',
        'MockUnitTests.CrashTest',
      ],
    )

  @patch.object(GTestTestBinary, 'run', autospec=True)
  def test_group_step_better_than_single_test_step(self, mock_test_binary_run):
    test_self = self

    def mock_test_binary_run_side_effect(self, *args, **kwargs):
      if len(self.tests) == 3:
        return test_self.generate_result_summary(
          'MockUnitTests.CrashTest',
          'C' + 'P' * (self.repeat - 1),
          duration=0,
        )
      return test_self.generate_result_summary(
        'MockUnitTests.CrashTest', 'PC' + 'P' * (self.repeat - 2), duration=0
      )

    mock_test_binary_run.side_effect = mock_test_binary_run_side_effect

    # Strategy should get all test as parallel during the failing test running
    # period.
    for test in self.result_summary:
      test.thread_id = None

    strategy = self.get_strategy('MockUnitTests.CrashTest')
    reproducing_step = strategy.run()
    # 3 repeat runs for ['FailTest', 'PassTest', 'CrashTest']
    # 3 repeat runs for ['PassTest', 'CrashTest']
    # 3 repeat runs for ['FailTest', 'CrashTest']
    self.assertEqual(mock_test_binary_run.call_count, 9)
    self.assertTrue(reproducing_step)
    self.assertListEqual(
      reproducing_step.test_binary.tests,
      [
        'MockUnitTests.FailTest',
        'MockUnitTests.PassTest',
        'MockUnitTests.CrashTest',
      ],
    )

  @patch.object(GTestTestBinary, 'run', autospec=True)
  def test_reproduce_group_step_reproduced_once(self, mock_test_binary_run):
    test_self = self

    def mock_test_binary_run_side_effect(self, *args, **kwargs):
      if len(self.tests) == 3 and mock_test_binary_run.call_count == 1:
        return test_self.generate_result_summary(
          'MockUnitTests.CrashTest',
          'C' + 'P' * (self.repeat - 1),
          duration=0,
        )
      return test_self.generate_result_summary(
        'MockUnitTests.CrashTest', 'P' * self.repeat, duration=0
      )

    mock_test_binary_run.side_effect = mock_test_binary_run_side_effect

    # Strategy should get all test as parallel during the failing test running
    # period.
    for test in self.result_summary:
      test.thread_id = None

    strategy = self.get_strategy('MockUnitTests.CrashTest')
    reproducing_step = strategy.run()
    # reproduced 3 times in group and 2 * MAX_ITERATIONS in single test
    self.assertEqual(mock_test_binary_run.call_count, 30)
    self.assertTrue(reproducing_step)
    self.assertListEqual(
      reproducing_step.test_binary.tests,
      [
        'MockUnitTests.FailTest',
        'MockUnitTests.PassTest',
        'MockUnitTests.CrashTest',
      ],
    )
    self.assertEqual(reproducing_step.reproduced_cnt, 1)
    # MAX_REPEAT * (MAX_ITERATIONS - 1) + 1
    self.assertEqual(reproducing_step.total_run_cnt, 91)

  @patch.object(GTestTestBinary, 'run', autospec=True)
  def test_reproduce_single_test_step(self, mock_test_binary_run):
    test_self = self

    def mock_test_binary_run_side_effect(self, *args, **kwargs):
      if 'MockUnitTests.FailTest' in self.tests:
        return test_self.generate_result_summary(
          'MockUnitTests.CrashTest',
          'C' + 'P' * (self.repeat - 1),
          duration=0,
        )
      return test_self.generate_result_summary(
        'MockUnitTests.CrashTest', 'P' * self.repeat, duration=0
      )

    mock_test_binary_run.side_effect = mock_test_binary_run_side_effect

    strategy = self.get_strategy('MockUnitTests.CrashTest')
    reproducing_step = strategy.run()
    # REPRODUCE_CNT times in group + REPRODUCE_CNT with FailTest in single test
    # + NOT_REPRODUCE_RETRY with PassTest in single test
    self.assertEqual(mock_test_binary_run.call_count, 8)
    self.assertTrue(reproducing_step)
    self.assertListEqual(
      reproducing_step.test_binary.tests,
      ['MockUnitTests.FailTest', 'MockUnitTests.CrashTest'],
    )
    self.assertEqual(reproducing_step.reproduced_cnt, 3)
    self.assertEqual(reproducing_step.total_run_cnt, 3)

  @patch.object(GTestTestBinary, 'run', autospec=True)
  def test_not_reproduced(self, mock_test_binary_run):
    mock_test_binary_run.return_value = self.generate_result_summary(
      'MockUnitTests.CrashTest', 'P'
    )
    strategy = self.get_strategy('MockUnitTests.CrashTest')
    reproducing_step = strategy.run()
    self.assertFalse(reproducing_step)
    # MAX_ITERATIONS
    self.assertEqual(mock_test_binary_run.call_count, 10)

  @patch.object(GTestTestBinary, 'run', autospec=True)
  def test_should_raise_if_target_test_not_run(self, mock_test_binary_run):
    mock_test_binary_run.return_value = self.generate_result_summary('', '')
    strategy = self.get_strategy('MockUnitTests.CrashTest')
    strategy.valid_for_test()
    with self.assertRaisesRegex(
      KeyError, "Target test wasn't executed during reproducing"
    ):
      strategy._verify_parallel_tests([strategy.failing_sample])
