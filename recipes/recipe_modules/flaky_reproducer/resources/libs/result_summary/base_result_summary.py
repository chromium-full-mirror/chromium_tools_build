# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import re
from enum import Enum


# Concept from:
# https://source.chromium.org/chromium/infra/infra/+/main:recipes-py/recipe_proto/go.chromium.org/luci/resultdb/proto/v1/test_result.proto;l=142;drc=e52acbc0987bdb31369aba51894b98286a418582
class TestStatus(Enum):
  """Machine-readable status of a test result."""

  # Status was not specified.
  # Not to be used in actual test results; serves as a default value for an
  # unset field.
  STATUS_UNSPECIFIED = 0

  # The test case has passed.
  PASS = 1

  # The test case has failed.
  # Suggests that the code under test is incorrect, but it is also possible
  # that the test is incorrect or it is a flake.
  FAIL = 2

  # The test case crashed during execution.
  # The outcome is inconclusive: the code under test might or might not be
  # correct, but the test+code is incorrect.
  CRASH = 3

  # The test case has started, but was aborted before finishing.
  # A common reason: timeout.
  ABORT = 4

  # The test case did not execute.
  # Examples:
  # - The execution of the collection of test cases, such as a test
  #   binary, was aborted prematurely and execution of some test cases was
  #   skipped.
  # - The test harness configuration specified that the test case MUST be
  #   skipped.
  SKIP = 5


class TestResult:
  """
  A result of a test case.
  Often a single test case is executed multiple times and has multiple results.

  Attributes:
    test_name (str): The test name of a test case.
      It must be the same name that is used as a test filter for the test suite.
      And it should be the same as the tag.test_name in ResultDB for test_id to
      test_name conversion works correctly.

    expected (bool): Whether the result of test case execution is expected.
      In a typical Chromium CL, 99%+ of test results are expected.
      Users are typically interested only in the unexpected results.
      An unexpected result != test case failure. There are test cases that are
      expected to fail/skip/crash. The test harness compares the actual status
      with the expected one(s) and this field is the result of the comparison.

    status (TestStatus): Machine-readable status of the test case.
      MUST NOT be STATUS_UNSPECIFIED.

    primary_error_message (str|None): The error message that ultimately caused
      the test to fail. This should only be the error message and should not
      include any stack traces.
      An example would be the message from an Exception in a Java test.
      In the case that a test failed due to multiple expectation failures, any
      immediately fatal failure should be chosen, or otherwise the first
      expectation failure.

    start_time (float|None): The point in time (in seconds) when the test case
      started to execute.

    duration (int|None): Duration in milliseconds of the test case execution.
      MUST be equal to or greater than 0.

    batch_id (int|None): An identifier of the process launched.
      The tests with same batch_id are running in a same process or execution
      container. That a tests could change the global state that affecting
      following tests.
      This could be a process id of the child process, or a sequence number of
      the child process in case the process id might be reused by the OS.
      This is an optional field and used by batch strategy. It can be omit if
      the test suite doesn't run in a batch.

    thread_id (int|None): The thread id of a runner that launches the tests in
      parallel.
      Although the concept might be different between OS and test suites. The
      tests with running duration overlap with different thread id are expected
      running in parallel.
      This is an optional field and used by parallel strategy.

  """

  __slots__ = (
    'test_name',
    'expected',
    'status',
    'primary_error_message',
    'start_time',
    'duration',
    'batch_id',
    'thread_id',
  )

  def __init__(
    self,
    test_name,
    expected=False,
    status=TestStatus.STATUS_UNSPECIFIED,
    primary_error_message=None,
    start_time=None,
    duration=None,
    batch_id=None,
    thread_id=None,
  ):
    self.test_name = test_name
    self.expected = expected
    self.status = status
    self.primary_error_message = primary_error_message
    self.start_time = start_time
    self.duration = duration
    self.batch_id = batch_id
    self.thread_id = thread_id

  def is_valid(self):
    return self.status != TestStatus.STATUS_UNSPECIFIED

  def __repr__(self):
    return "{0}({1}) - {2}".format(
      self.status.name,
      'expected' if self.expected else 'unexpected',
      self.test_name,
    )

  def similar_with(self, other):
    """Return if TestResult similar with the other test.

    ResultSummary of different test harness might override this method to
    provide customized TestResult comparison.
    """
    return self.status == other.status


class TestResultErrorMessageRegexSimilarityMixin:
  """Implements Luci Analysis regex test reason clustering algorithm, that
  ignore the numbers in the error message: http://go/weetbix-bugs-dd.
  """

  def similar_with(self, other):
    if not super().similar_with(other):
      return False
    if self.primary_error_message == other.primary_error_message:
      return True
    if self.primary_error_message and other.primary_error_message:
      remove_number = re.compile(r'([0-9]+|[0-9a-fx]{8,})', re.IGNORECASE)
      a_message = remove_number.sub('0', self.primary_error_message)
      b_message = remove_number.sub('0', other.primary_error_message)
      if a_message == b_message:
        return True
    return False


class UnexpectedTestResult(TestResult):
  """A TestResult that similar_with any unexpected results.

  It's a helper class to accept any unexpected TestResult while reproducing when
  no TestResult could be found.
  """

  def similar_with(self, other):
    return not other.expected


class BaseResultSummary:
  """
  Collection of all test results within a run.
  """

  def __init__(self):
    self._results = []

  def __iter__(self):
    return iter(self._results)

  def __contains__(self, test_name):
    return any(r.test_name == test_name for r in self)

  def __len__(self):
    return len(self._results)

  def add(self, test_result):
    assert isinstance(test_result, TestResult)
    self._results.append(test_result)

  def get_all(self, test_name):
    """Get all TestResult that matches the given test_name in start_time order.

    Args:
      test_name (str): The name of the test.

    Returns:
      A list of TestResult of all tests matching the given test_name.
    """
    # Python sort is stable, if result doesn't have start_time, the sorted
    # result should keep the same.
    return sorted(
      [r for r in self if r.test_name == test_name],
      key=lambda r: r.start_time or 0,
    )

  def get_failing_sample(self, test_name, default=UnexpectedTestResult):
    """Get an unexpected sample for |test_name|, or |default| if not found."""
    for r in self.get_all(test_name):
      if not r.expected:
        return r
    if default is UnexpectedTestResult:
      return UnexpectedTestResult(test_name)
    return default

  def dump_raw_data(self):
    """Return the raw data of the result summary as string"""
    raise NotImplementedError('Method should be implemented in sub-classes.')
