# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from libs.result_summary import TestStatus
from libs.result_summary.blink_web_tests_result_summary import (
  BlinkWebTestsResultSummary,
)
from testdata import get_test_data


class BlinkWebTestsResultSummaryTest(unittest.TestCase):
  def test_run_histories_json(self):
    json_data = json.loads(get_test_data('blink_web_tests_result_summary.json'))
    result_summary = BlinkWebTestsResultSummary.from_output_json(json_data)
    self.assertEqual(result_summary.dump_raw_data(), json.dumps(json_data))

    self.assertEqual(len(result_summary), 23)
    self.assertIn('passes/text.html', result_summary)
    results = result_summary.get_all('passes/text.html')
    self.assertEqual(len(results), 4)
    self.assertEqual(results[0].test_name, 'passes/text.html')
    self.assertEqual(results[0].expected, False)
    self.assertEqual(results[0].status, TestStatus.ABORT)
    self.assertEqual(results[0].primary_error_message, 'test timed out')
    self.assertEqual(results[1].expected, True)
    self.assertEqual(results[1].status, TestStatus.PASS)
    self.assertEqual(results[1].primary_error_message, None)

    results = result_summary.get_all('failures/expected/audio.html')
    self.assertEqual(results[-1].expected, True)
    self.assertEqual(results[-1].status, TestStatus.FAIL)

    results = result_summary.get_all('failures/expected/crash.html')
    self.assertEqual(results[2].expected, True)
    self.assertEqual(results[2].status, TestStatus.CRASH)
    self.assertEqual(results[3].expected, False)
    self.assertEqual(results[3].status, TestStatus.FAIL)
    self.assertEqual(
      results[3].primary_error_message, 'some primary error message'
    )

  @patch('logging.error')
  def test_corrupted_output_json(self, mock_logging):
    with self.assertRaisesRegex(
      ValueError, 'Not supported Blink Web Tests output format'
    ):
      BlinkWebTestsResultSummary.from_output_json({})

    with self.assertRaisesRegex(
      ValueError, 'Not supported Blink Web Tests output format'
    ):
      BlinkWebTestsResultSummary.from_output_json({'run_histories': [{}]})
    mock_logging.assert_called()
