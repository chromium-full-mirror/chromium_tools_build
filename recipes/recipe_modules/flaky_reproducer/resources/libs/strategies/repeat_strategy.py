# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import logging
import math
import time

from . import utils
from .base_strategy import BaseStrategy


class RepeatStrategy(BaseStrategy):
  name = 'repeat'

  MAX_RETRIES = 200
  SINGLE_ROUND_RETRIES = 20
  SINGLE_ROUND_SECONDS = 5 * 60

  def run(self, timeout=45 * 60):
    deadline = time.time() + timeout - self.SINGLE_ROUND_SECONDS

    # Reproduces up to 3 times or MAX_RETRIES or reaches the deadline.
    reproduced = 0
    running_history = []
    failing_sample = self.result_summary.get_failing_sample(self.test_name)
    logging.info(
      'Running %s strategy for %s with reason: %s',
      self.name,
      self.test_name,
      failing_sample.primary_error_message,
    )
    single_round_retries = self._calc_single_round_retries()
    iteration = 0
    while (
      reproduced < 3
      and len(running_history) < self.MAX_RETRIES
      and time.time() < deadline
    ):
      iteration += 1
      result = (
        self.test_binary.with_tests(  # go/pyformat-break
          [self.test_name]
        ).with_repeat(single_round_retries)  #  #
      ).run()
      test_history = result.get_all(self.test_name)
      if not test_history:
        raise KeyError(
          "Target test wasn't executed during reproducing: {0}".format(
            self.test_name
          )
        )
      # Ignore the runs after first failure.
      # As flaky reproducer, we care about the first reproduction. Tests could
      # fall into a bad state after first failure that cause false high
      # reproducing rate.
      for r in test_history:
        running_history.append(r)
        if failing_sample.similar_with(r):
          reproduced += 1
          break
      logging.info(
        'iteration %d: reproduced=%d/%d',
        iteration,
        reproduced,
        len(running_history),
      )
      single_round_retries = self._calc_single_round_retries(running_history)
    return self._generate_reproducing_step(reproduced, running_history)

  def _calc_single_round_retries(self, running_history=None):
    """Limit the run time of each round within SINGLE_ROUND_SECONDS."""
    if not running_history:
      running_history = self.result_summary.get_all(self.test_name)
    avg_duration = utils.avg_duration(running_history)
    if not avg_duration:
      return self.SINGLE_ROUND_RETRIES
    return math.floor(
      min(
        self.SINGLE_ROUND_RETRIES,
        self.SINGLE_ROUND_SECONDS * 1000 / avg_duration,
      )
    )

  def _generate_reproducing_step(self, reproduced_cnt, running_history):
    if not running_history:
      raise Exception(
        'Cannot generate reproducing step without running history.'
      )

    # Use minimum 1ms for average duration that the maximum retries equal to the
    # milliseconds of reproducing time limit.
    avg_duration = utils.avg_duration(running_history) or 1
    max_retries = math.ceil(
      self.TARGET_REPRODUCING_TIME_LIMIT * 1000.0 / avg_duration
    )
    failure_rate = reproduced_cnt * 1.0 / len(running_history)
    suggested_repeat = min(
      self.MAX_RETRIES,
      max_retries,
      utils.calc_repeat_times_based_on_failing_rate(
        self.TARGET_REPRODUCING_RATE, failure_rate
      ),
    )
    reproducing_rate = 1.0 - (1.0 - failure_rate) ** suggested_repeat
    test_binary = (
      self.test_binary.with_tests(  # go/pyformat-break
        [self.test_name]
      ).with_repeat(suggested_repeat)  #  #
    )
    return self._reproducing_step(
      test_binary,
      reproducing_rate=reproducing_rate,
      duration=avg_duration * suggested_repeat,
      reproduced_cnt=reproduced_cnt,
      total_run_cnt=len(running_history),
    )
