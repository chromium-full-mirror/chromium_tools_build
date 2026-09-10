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
from libs.strategies.batch_strategy import BatchStrategy
from testdata import get_test_data
from unittests import GenerateResultSummaryMixin


class BatchStrategyTest(unittest.TestCase, GenerateResultSummaryMixin):
  def setUp(self):
    self.test_binary = create_test_binary_from_jsonish(
      json.loads(get_test_data('gtest_test_binary.json'))
    )
    self.result_summary = create_result_summary_from_output_json(
      json.loads(get_test_data('gtest_good_output.json'))
    )

  def get_strategy(self, test_name, test_binary=None, result_summary=None):
    test_binary = test_binary or self.test_binary
    result_summary = result_summary or self.result_summary
    return BatchStrategy(test_binary, result_summary, test_name)

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

    failing_sample = self.result_summary.get_failing_sample(
      'MockUnitTests.CrashTest'
    )
    failing_sample.batch_id = 654321
    self.assertFalse(
      self.get_strategy('MockUnitTests.CrashTest').valid_for_test()
    )
    failing_sample.batch_id = 0

  def test_no_batch_id(self):
    strategy = self.get_strategy('MockUnitTests.AnyTest')
    with self.assertRaisesRegex(
      Exception,
      'Batch strategy requires failing sample with not None batch_id.',
    ):
      strategy.run()

  def test_get_batch_tests(self):
    strategy = self.get_strategy('MockUnitTests.CrashTest')
    strategy.failing_sample = strategy.result_summary.get_failing_sample(
      strategy.test_name
    )
    tests = strategy._get_batch_tests()
    self.assertEqual(
      ['MockUnitTests.FailTest', 'MockUnitTests.PassTest'],
      [x.test_name for x in tests],
    )

    for t in self.result_summary.get_all('MockUnitTests.FailTest'):
      t.start_time += 10
    tests = strategy._get_batch_tests()
    self.assertEqual(len(tests), 1)
    self.assertEqual(['MockUnitTests.PassTest'], [x.test_name for x in tests])

  def test_timeout(self):
    strategy = self.get_strategy('MockUnitTests.CrashTest')
    reproducing_step = strategy.run(timeout=0)
    self.assertFalse(reproducing_step)

  def test_empty_batch_tests(self):
    failing_sample = self.result_summary.get_failing_sample(
      'MockUnitTests.CrashTest'
    )
    failing_sample.batch_id = 654321
    strategy = self.get_strategy('MockUnitTests.CrashTest')
    reproducing_step = strategy.run()
    self.assertFalse(reproducing_step)

  @patch.object(GTestTestBinary, 'run', autospec=True)
  def test_find_best_batch_tests(self, mock_test_binary_run):
    # The strategy should verify FailTest + PassTest with CrashTest
    test_self = self

    def mock_test_binary_run_side_effect(self, *args, **kwargs):
      if len(self.tests) == 3:
        return test_self.generate_result_summary(
          'MockUnitTests.CrashTest',
          'C',
        )
      return test_self.generate_result_summary('MockUnitTests.CrashTest', 'P')

    mock_test_binary_run.side_effect = mock_test_binary_run_side_effect

    strategy = self.get_strategy('MockUnitTests.CrashTest')
    reproducing_step = strategy.run()
    self.assertEqual(
      reproducing_step.test_binary.tests,
      [
        'MockUnitTests.FailTest',
        'MockUnitTests.PassTest',
        'MockUnitTests.CrashTest',
      ],
    )
    self.assertEqual(mock_test_binary_run.call_count, 30)

  @patch.object(GTestTestBinary, 'run', autospec=True)
  def test_recursive_find_best_batch_tests(self, mock_test_binary_run):
    # The strategy should verify CrashTest + PassTest with FailTest
    test_self = self

    def mock_test_binary_run_side_effect(self, *args, **kwargs):
      if 'MockUnitTests.FailTest' in self.tests:
        return test_self.generate_result_summary(
          'MockUnitTests.CrashTest',
          'C',
        )
      return test_self.generate_result_summary('MockUnitTests.CrashTest', 'P')

    mock_test_binary_run.side_effect = mock_test_binary_run_side_effect

    failing_sample = self.result_summary.get_failing_sample(
      'MockUnitTests.CrashTest'
    )
    # 1 mins, 1/5 of SINGLE_ROUND_SECONDS, this will limit the repeat to 5
    failing_sample.duration = 60 * 1000

    strategy = self.get_strategy('MockUnitTests.CrashTest')
    reproducing_step = strategy.run()
    # 5 repeat runs for ['FailTest', 'PassTest', 'CrashTest'] and reproduced
    # 5 repeat runs for ['PassTest', 'CrashTest'] but not reproduced
    # 5 repeat runs for ['FailTest', 'CrashTest'] and reproduced
    self.assertEqual(mock_test_binary_run.call_count, 15)
    self.assertEqual(
      reproducing_step.test_binary.tests,
      ['MockUnitTests.FailTest', 'MockUnitTests.CrashTest'],
    )

  @patch.object(GTestTestBinary, 'run')
  def test_not_executed_error(self, mock_test_binary_run):
    mock_test_binary_run.return_value = self.generate_result_summary(None, '')
    strategy = self.get_strategy('MockUnitTests.CrashTest')
    with self.assertRaisesRegex(
      KeyError, "Target test wasn't executed during reproducing"
    ):
      strategy.run()

  @patch.object(GTestTestBinary, 'run', autospec=True)
  def test_strategy_should_only_verify_the_second_half(
    self, mock_test_binary_run
  ):
    # The strategy should verify CrashTest + PassTest with FailTest
    test_self = self

    def mock_test_binary_run_side_effect(self, *args, **kwargs):
      assert 'MockUnitTests.PassTest' in self.tests
      return test_self.generate_result_summary(
        'MockUnitTests.CrashTest',
        'C',
      )

    mock_test_binary_run.side_effect = mock_test_binary_run_side_effect

    strategy = self.get_strategy('MockUnitTests.CrashTest')
    reproducing_step = strategy.run()
    # 10 repeat runs for ['FailTest', 'PassTest', 'CrashTest'] and reproduced
    # 10 repeat runs for ['PassTest', 'CrashTest'] and reproduced
    self.assertEqual(mock_test_binary_run.call_count, 20)
    self.assertEqual(
      reproducing_step.test_binary.tests,
      ['MockUnitTests.PassTest', 'MockUnitTests.CrashTest'],
    )
