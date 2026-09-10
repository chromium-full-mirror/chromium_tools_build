# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from libs.result_summary import TestStatus
from libs.result_summary.gtest_result_summary import (
  GTestTestResultSummary,
  GTestTestResult,
)


class GenerateResultSummaryMixin:
  SEQ_LETTER_TO_STATUS = {
    'P': TestStatus.PASS,
    'F': TestStatus.FAIL,
    'C': TestStatus.CRASH,
    'A': TestStatus.ABORT,
    'S': TestStatus.SKIP,
  }

  def generate_result_summary(self, test_name, seq, **kwargs):
    result_summary = GTestTestResultSummary()
    for s in seq:
      result_summary.add(
        GTestTestResult(
          test_name,
          expected=(s == 'P'),
          status=self.SEQ_LETTER_TO_STATUS[s],
          **kwargs,
        )
      )
    return result_summary
