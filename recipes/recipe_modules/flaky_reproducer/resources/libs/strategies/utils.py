# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import math


def avg_duration(running_history):
  """Calculates the average TestResult.duration.

  Args:
    running_history (list[TestResult]): List of TestResults

  Returns:
    Average duration in milliseconds.
    Or None if no samples or all of the tests were running less than 1 ms that
    reporting 0 as duration.
  """
  durations = [r.duration for r in running_history if r.duration]
  if not durations:
    return None
  return sum(durations) * 1.0 / len(durations)


def calc_repeat_times_based_on_failing_rate(
  target_reproducing_rate, failure_rate
):
  """Based on the [failure_rate] for an individual run, returns the number of
  repeat times that would result a [target_reproducing_rate]."""
  if failure_rate <= 0:
    return math.inf
  if failure_rate >= 1:
    return 1
  return math.ceil(
    math.log(1 - target_reproducing_rate) / math.log(1 - failure_rate)
  )
