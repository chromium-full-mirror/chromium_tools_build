# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from libs.test_binary import create_test_binary_from_jsonish
from libs.test_binary.gtest_test_binary import GTestTestBinary
from libs.result_summary import create_result_summary_from_output_json
from libs.strategies.repeat_strategy import RepeatStrategy
from testdata import get_test_data
from unittests import GenerateResultSummaryMixin


class RepeatStrategyTest(unittest.TestCase, GenerateResultSummaryMixin):
  def setUp(self):
    self.test_binary = create_test_binary_from_jsonish(
      json.loads(get_test_data('gtest_test_binary.json'))
    )
    self.result_summary = create_result_summary_from_output_json(
      json.loads(get_test_data('gtest_good_output.json'))
    )

  @patch.object(GTestTestBinary, 'run')
  @patch.dict(
    'os.environ',
    {
      'SWARMING_SERVER': 'http://example.org',
      'SWARMING_TASK_ID': '123',
    },
    clear=True,
  )
  def test_run_reproduced(self, mock_test_binary_run):
    strategy = RepeatStrategy(
      self.test_binary, self.result_summary, 'MockUnitTests.AnyTest'
    )
    mock_test_binary_run.return_value = self.generate_result_summary(
      'MockUnitTests.AnyTest', 'PPPFP', duration=2
    )
    reproducing_step = strategy.run()
    self.assertEqual(mock_test_binary_run.call_count, 3)
    self.assertEqual(reproducing_step.reproduced_cnt, 3)
    # The P after F is ignored.
    self.assertEqual(reproducing_step.total_run_cnt, 12)
    self.assertEqual(
      reproducing_step.debug_info['task_ui_link'],
      'http://example.org/task?id=123',
    )

  @patch.object(GTestTestBinary, 'run')
  def test_run_raise_error_if_no_test_executed(self, mock_test_binary_run):
    strategy = RepeatStrategy(
      self.test_binary, self.result_summary, 'MockUnitTests.FailTest'
    )
    mock_test_binary_run.return_value = self.generate_result_summary(
      'MockUnitTests.FailTest', ''
    )
    with self.assertRaisesRegex(
      KeyError, r"Target test wasn't executed during reproducing: .*"
    ):
      strategy.run()

  @patch.object(GTestTestBinary, 'run')
  def test_run_not_reproduced_message_not_match(self, mock_test_binary_run):
    strategy = RepeatStrategy(
      self.test_binary, self.result_summary, 'MockUnitTests.FailTest'
    )
    mock_test_binary_run.return_value = self.generate_result_summary(
      'MockUnitTests.FailTest', 'PPPFP'
    )
    reproducing_step = strategy.run()
    # MAX_RETRIES / len('PPPFP')
    self.assertEqual(mock_test_binary_run.call_count, 40)
    self.assertEqual(reproducing_step.reproduced_cnt, 0)
    self.assertEqual(reproducing_step.total_run_cnt, strategy.MAX_RETRIES)

  @patch.object(GTestTestBinary, 'run')
  def test_run_reproduced_message_match(self, mock_test_binary_run):
    strategy = RepeatStrategy(
      self.test_binary, self.result_summary, 'MockUnitTests.FailTest'
    )
    mock_test_binary_run.return_value = self.generate_result_summary(
      'MockUnitTests.FailTest',
      'PPPFP',
      primary_error_message="Value of: false\n  Actual: false\nExpected: true",
    )
    reproducing_step = strategy.run()
    self.assertEqual(mock_test_binary_run.call_count, 3)
    self.assertEqual(reproducing_step.reproduced_cnt, 3)
    # P after F is ignored.
    self.assertEqual(reproducing_step.total_run_cnt, 12)

  @patch('time.time')
  @patch.object(GTestTestBinary, 'run')
  def test_run_timeout(self, mock_test_binary_run, mock_time):
    strategy = RepeatStrategy(
      self.test_binary, self.result_summary, 'MockUnitTests.AnyTest'
    )
    mock_time.return_value = 0

    def mocked_test_binary_run(*_, **__):
      mock_time.return_value += 60
      return self.generate_result_summary(
        'MockUnitTests.AnyTest', 'PPPPP', duration=2
      )

    mock_test_binary_run.side_effect = mocked_test_binary_run
    strategy.run(timeout=15 * 60)
    # (timeout - SINGLE_ROUND_SECONDS) / (mock_time per run = 60)
    self.assertEqual(mock_test_binary_run.call_count, 10)

  def test_generate_reproducing_step(self):
    strategy = RepeatStrategy(
      self.test_binary, self.result_summary, 'MockUnitTests.AnyTest'
    )
    with self.assertRaisesRegex(
      Exception, 'Cannot generate reproducing step without running history.'
    ):
      strategy._generate_reproducing_step(0, [])
