# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import json
import unittest

from libs.result_summary import TestStatus
from libs.result_summary.gtest_result_summary import (
  GTestTestResultSummary,
  extract_primary_error_message,
)
from testdata import get_test_data


class GTestTestResultSummaryFactoryTest(unittest.TestCase):
  def test_good_output_json(self):
    json_data = json.loads(get_test_data('gtest_good_output.json'))
    result_summary = GTestTestResultSummary.from_output_json(json_data)
    self.assertEqual(result_summary.dump_raw_data(), json.dumps(json_data))

    self.assertEqual(len(result_summary), 4)

    self.assertIn('MockUnitTests.CrashTest', result_summary)
    results = result_summary.get_all('MockUnitTests.CrashTest')
    self.assertEqual(len(results), 1)
    self.assertEqual(results[0].test_name, 'MockUnitTests.CrashTest')
    self.assertEqual(results[0].expected, False)
    self.assertEqual(results[0].status, TestStatus.CRASH)
    self.assertEqual(results[0].primary_error_message, None)
    self.assertEqual(results[0].start_time, 1653693897.211)
    self.assertEqual(results[0].duration, 0)
    self.assertEqual(results[0].batch_id, 0)
    self.assertEqual(results[0].thread_id, 3026892)

    self.assertIn('MockUnitTests.FailTest', result_summary)
    results = result_summary.get_all('MockUnitTests.FailTest')
    self.assertEqual(len(results), 1)
    self.assertEqual(results[0].test_name, 'MockUnitTests.FailTest')
    self.assertEqual(results[0].expected, False)
    self.assertEqual(results[0].status, TestStatus.FAIL)
    self.assertEqual(
      results[0].primary_error_message,
      "Value of: false\n  Actual: false\nExpected: true",
    )
    self.assertEqual(results[0].start_time, 1653693897.210)
    self.assertEqual(results[0].duration, 0)
    self.assertEqual(results[0].batch_id, 0)
    self.assertEqual(results[0].thread_id, 3026892)

    self.assertIn('MockUnitTests.NoRunTest', result_summary)
    results = result_summary.get_all('MockUnitTests.NoRunTest')
    self.assertEqual(len(results), 1)
    self.assertEqual(results[0].test_name, 'MockUnitTests.NoRunTest')
    self.assertEqual(results[0].expected, False)
    self.assertEqual(results[0].status, TestStatus.SKIP)
    self.assertEqual(results[0].primary_error_message, None)
    self.assertEqual(results[0].start_time, None)
    self.assertEqual(results[0].duration, 0)
    self.assertEqual(results[0].batch_id, None)
    self.assertEqual(results[0].thread_id, None)

    self.assertIn('MockUnitTests.PassTest', result_summary)
    results = result_summary.get_all('MockUnitTests.PassTest')
    self.assertEqual(len(results), 1)
    self.assertEqual(results[0].test_name, 'MockUnitTests.PassTest')
    self.assertEqual(results[0].expected, True)
    self.assertEqual(results[0].status, TestStatus.PASS)
    self.assertEqual(results[0].primary_error_message, None)
    self.assertEqual(results[0].start_time, 1653693897.210)
    self.assertEqual(results[0].duration, 0)
    self.assertEqual(results[0].batch_id, 0)
    self.assertEqual(results[0].thread_id, 3026892)

  def test_extract_primary_error_message_from_failure(self):
    result_parts = [
      {"summary": "error from success", "type": "success"},
      {"summary": "error from failure", "type": "failure"},
      {"summary": "error from fatal_failure", "type": "fatal_failure"},
    ]
    self.assertEqual(
      extract_primary_error_message(result_parts), 'error from fatal_failure'
    )
    self.assertEqual(
      extract_primary_error_message(result_parts[:2]), 'error from failure'
    )

  def test_corrupted_output_json_1(self):
    with self.assertRaises(ValueError):
      GTestTestResultSummary.from_output_json({})

  def test_corrupted_output_json_2(self):
    with self.assertRaises(ValueError):
      GTestTestResultSummary.from_output_json(
        {
          'per_iteration_data': [None],
        }
      )
