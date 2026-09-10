# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import logging
import math
import time

from .base_strategy import BaseStrategy
from ..test_binary import TestBinaryWithBatchMixin


class BatchStrategy(BaseStrategy):
  name = 'batch'

  MAX_REPEAT = 10
  SINGLE_ROUND_SECONDS = 5 * 60

  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self.failing_sample = None
    self.deadline = None
    self.repeat = self.MAX_REPEAT

  def valid_for_test(self):
    if not isinstance(self.test_binary, TestBinaryWithBatchMixin):
      return False
    self.failing_sample = self.result_summary.get_failing_sample(
      self.test_name, default=None
    )
    if not self.failing_sample or self.failing_sample.batch_id is None:
      return False
    batch_tests = self._get_batch_tests()
    if not batch_tests:
      return False
    return True

  def run(self, timeout=45 * 60):
    self.failing_sample = self.result_summary.get_failing_sample(
      self.test_name, default=None
    )
    if not self.failing_sample or self.failing_sample.batch_id is None:
      raise Exception(
        'Batch strategy requires failing sample with not None batch_id.'
      )
    logging.info(
      'Running %s strategy for %s with reason: %s',
      self.name,
      self.test_name,
      self.failing_sample.primary_error_message,
    )
    self.deadline = time.time() + timeout - self.SINGLE_ROUND_SECONDS
    batch_tests = self._get_batch_tests()
    total_duration = sum(t.duration or 0 for t in batch_tests) + (
      self.failing_sample.duration or 0
    )
    if total_duration:
      self.repeat = min(
        self.MAX_REPEAT,
        math.floor(self.SINGLE_ROUND_SECONDS * 1000 / total_duration),
      )
    self.repeat = max(self.repeat, 1)
    return self._find_best_batch_tests(batch_tests)

  def _find_best_batch_tests(self, tests):
    if not tests or time.time() > self.deadline:
      return self._reproducing_step(self.test_binary, reproducing_rate=0)
    # verify if the current tests reproduce the failure.
    start_time = time.time()
    reproduced, test_binary, test_history = self._verify_batch_tests(tests)
    total_time = time.time() - start_time
    reproduce_step = self._reproducing_step(
      test_binary,
      reproducing_rate=reproduced * 1.0 / len(test_history),
      duration=total_time * 1000.0 / self.repeat,
      reproduced_cnt=reproduced,
      total_run_cnt=len(test_history),
    )
    if not reproduce_step or len(tests) == 1:
      return reproduce_step
    # bisect tests to reduce tests needed to reproduce.
    # The tests that runs closer to the failing sample have higher possibility
    # causes the flake.
    reproduce_step_high = self._find_best_batch_tests(tests[len(tests) // 2 :])
    # Skip verify the first half if it has already reproduced.
    if reproduce_step_high and reproduce_step_high.reproducing_rate == 1.0:
      return reproduce_step_high
    reproduce_step_low = self._find_best_batch_tests(tests[: len(tests) // 2])
    if reproduce_step_low or reproduce_step_high:
      return (
        reproduce_step_low
        if reproduce_step_low.better_than(reproduce_step_high)
        else reproduce_step_high
      )
    return reproduce_step

  def _verify_batch_tests(self, tests):
    """Verify if candidate batch reproducible."""
    assert self.failing_sample
    assert self.repeat
    test_history = []
    test_binary = (
      self.test_binary.with_tests(  # go/pyformat-break
        [t.test_name for t in tests] + [self.test_name]
      ).with_single_batch()  #  #
    )
    for _ in range(self.repeat):
      result = test_binary.run()
      test_history += result.get_all(self.test_name)
    if not test_history:
      raise KeyError(
        "Target test wasn't executed during reproducing: {0}".format(
          self.test_name
        )
      )
    reproduced = 0
    for test in test_history:
      if self.failing_sample.similar_with(test):
        reproduced += 1
    logging.info(
      'verify_batch_tests(%r) => reproduced=%d/%d:%s',
      test_binary.tests,
      reproduced,
      self.repeat,
      ''.join([t.status.name[0] for t in test_history]),
    )
    return reproduced, test_binary, test_history

  def _get_batch_tests(self):
    """Get tests that run in the same batch before the failing sample."""
    assert self.failing_sample
    batch_tests = []
    for test in self.result_summary:
      if test.test_name == self.failing_sample.test_name:
        continue
      if test.batch_id == self.failing_sample.batch_id:
        # Ignore tests ran after failing sample.
        if self.failing_sample.start_time and (
          test.start_time is None
          or test.start_time > self.failing_sample.start_time
        ):
          continue
        batch_tests.append(test)
    return sorted(batch_tests, key=lambda t: t.start_time or 0)
