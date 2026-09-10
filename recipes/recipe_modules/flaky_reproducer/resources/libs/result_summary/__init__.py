# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from .base_result_summary import (
  BaseResultSummary,
  TestStatus,
  TestResult,
  UnexpectedTestResult,
)
from .blink_web_tests_result_summary import BlinkWebTestsResultSummary
from .gtest_result_summary import GTestTestResultSummary


def create_result_summary_from_output_json(json_data):
  """Factory method for TestResultSummary(s) that distinguish and create the
  correct TestResultSummary object."""
  if 'run_histories' in json_data:
    return BlinkWebTestsResultSummary.from_output_json(json_data)
  if 'per_iteration_data' in json_data:
    return GTestTestResultSummary.from_output_json(json_data)
  raise NotImplementedError('Not supported output.json format.')
