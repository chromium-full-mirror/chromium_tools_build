# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from libs.result_summary import create_result_summary_from_output_json
from libs.result_summary.base_result_summary import (
  BaseResultSummary,
  TestResultErrorMessageRegexSimilarityMixin,
  TestResult,
  TestStatus,
  UnexpectedTestResult,
)
from libs.result_summary.gtest_result_summary import GTestTestResultSummary
from libs.result_summary.blink_web_tests_result_summary import (
  BlinkWebTestsResultSummary,
)
from testdata import get_test_data


class ResultSummaryFactoryTest(unittest.TestCase):
  @patch.object(GTestTestResultSummary, 'from_output_json')
  def test_gtest(self, mock_from_output_json):
    json_data = json.loads(get_test_data('gtest_good_output.json'))
    create_result_summary_from_output_json(json_data)
    mock_from_output_json.assert_called_with(json_data)

  @patch.object(BlinkWebTestsResultSummary, 'from_output_json')
  def test_blink_web_tests(self, mock_from_output_json):
    json_data = json.loads(get_test_data('blink_web_tests_result_summary.json'))
    create_result_summary_from_output_json(json_data)
    mock_from_output_json.assert_called_with(json_data)

  def test_unsupported(self):
    with self.assertRaisesRegex(NotImplementedError, 'Not supported'):
      create_result_summary_from_output_json({})

  def test_test_result(self):
    pass_result = TestResult('test.foo.bar.pass', True, TestStatus.PASS)
    self.assertTrue(pass_result.is_valid())
    self.assertEqual(repr(pass_result), 'PASS(expected) - test.foo.bar.pass')

    fail_result = TestResult('test.foo.bar.fail', False, TestStatus.FAIL)
    self.assertTrue(fail_result.is_valid())
    self.assertEqual(repr(fail_result), 'FAIL(unexpected) - test.foo.bar.fail')

    skip_result = TestResult('test.foo.bar.skip', True, TestStatus.SKIP)
    self.assertTrue(skip_result.is_valid())
    self.assertEqual(repr(skip_result), 'SKIP(expected) - test.foo.bar.skip')

    invalid_result = TestResult(
      'test.foo.bar.invalid', status=TestStatus.STATUS_UNSPECIFIED
    )
    self.assertFalse(invalid_result.is_valid())
    self.assertEqual(
      repr(invalid_result),
      'STATUS_UNSPECIFIED(unexpected) - test.foo.bar.invalid',
    )


class TestResultTest(unittest.TestCase):
  def test_similar_with(self):
    pass_result = TestResult('test.foo.bar.pass', True, TestStatus.PASS)
    fail_result = TestResult(
      'test.foo.bar.fail',
      False,
      TestStatus.FAIL,
      primary_error_message='failed with foo',
    )
    fail_result_bar = TestResult(
      'test.foo.bar.fail',
      False,
      TestStatus.FAIL,
      primary_error_message='failed with bar',
    )
    self.assertFalse(pass_result.similar_with(fail_result))
    self.assertTrue(fail_result.similar_with(fail_result_bar))

  def test_error_msg_similar_with(self):

    class NewTestResult(TestResultErrorMessageRegexSimilarityMixin, TestResult):
      pass

    pass_result = NewTestResult('test.foo.bar.pass', True, TestStatus.PASS)
    fail_result = NewTestResult(
      'test.foo.bar.fail',
      False,
      TestStatus.FAIL,
      primary_error_message='failed with foo',
    )
    self.assertFalse(pass_result.similar_with(fail_result))

    fail_result_foo = NewTestResult(
      'test.foo.bar.fail',
      False,
      TestStatus.FAIL,
      primary_error_message='failed with foo',
    )
    fail_result_bar = NewTestResult(
      'test.foo.bar.fail',
      False,
      TestStatus.FAIL,
      primary_error_message='failed with bar',
    )
    self.assertTrue(fail_result.similar_with(fail_result_foo))
    self.assertFalse(fail_result.similar_with(fail_result_bar))

  def test_error_msg_similar_with_num(self):

    class NewTestResult(TestResultErrorMessageRegexSimilarityMixin, TestResult):
      pass

    fail_result = NewTestResult(
      'test.foo.bar.fail',
      False,
      TestStatus.FAIL,
      primary_error_message='failed with 21',
    )
    fail_result_43 = NewTestResult(
      'test.foo.bar.fail',
      False,
      TestStatus.FAIL,
      primary_error_message='failed with 43',
    )
    self.assertTrue(fail_result.similar_with(fail_result_43))


class BaseResultSummaryTest(unittest.TestCase):
  def test_get_failing_sample(self):
    result_summary = BaseResultSummary()
    pass_test = TestResult('test.foo.bar.pass', True, TestStatus.PASS)
    fail_test = TestResult('test.foo.bar.fail', False, TestStatus.FAIL)
    result_summary.add(pass_test)
    result_summary.add(fail_test)

    self.assertIsInstance(
      result_summary.get_failing_sample('test.foo.bar.pass'),
      UnexpectedTestResult,
    )
    self.assertIsNone(
      result_summary.get_failing_sample('test.foo.bar.pass', default=None)
    )
    self.assertIsNone(
      result_summary.get_failing_sample('test.not.exists', default=None)
    )
    self.assertIs(
      result_summary.get_failing_sample('test.foo.bar.pass', default=fail_test),
      fail_test,
    )
    self.assertIs(
      result_summary.get_failing_sample('test.foo.bar.fail'), fail_test
    )

  def test_should_not_implement_in_base(self):
    result_summary = BaseResultSummary()
    with self.assertRaises(NotImplementedError):
      result_summary.dump_raw_data()
