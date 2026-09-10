# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from ..test_binary import create_test_binary_from_jsonish


class ReproducingStep:
  """Containing the information needed to reproduce a flaky test failure.

  Attributes:
    test_binary (TestBinary): The test binary with preset settings using with_*
      methods.
    strategy (str): The strategy name that generating the step.
    reproducing_rate (float): Reproducing rate by running the given test binary.
      This is different from failure rate, as failed runs / total runs. A test
      binary could rerun the test multiple times and only need to fail once to
      be counted as reproduced.
    duration (int): Duration in milliseconds of the total running time of the
      given test binary. It should contain all the repeats included in test
      binary options.
    reproduced_cnt (int): Number of times the failure reproduced during
      verification.
    total_run_cnt (int): Number of run times during verification.
  """

  def __init__(
    self,
    test_binary,
    strategy,
    reproducing_rate=0,
    duration=0,
    reproduced_cnt=0,
    total_run_cnt=0,
    **other,
  ):
    self.test_binary = test_binary
    self.reproducing_rate = reproducing_rate
    self.duration = duration
    self.strategy = strategy
    self.reproduced_cnt = reproduced_cnt
    self.total_run_cnt = total_run_cnt
    self.debug_info = other

  def __bool__(self):
    """Return true if reproduced."""
    return self.reproducing_rate > 0.0

  def to_jsonish(self):
    ret = dict(
      strategy=self.strategy,
      test_binary=self.test_binary.to_jsonish(),
      reproducing_rate=self.reproducing_rate,
      duration=self.duration,
      reproduced_cnt=self.reproduced_cnt,
      total_run_cnt=self.total_run_cnt,
    )
    ret.update(self.debug_info)
    return ret

  @classmethod
  def from_jsonish(cls, json_data):
    test_binary = create_test_binary_from_jsonish(json_data.pop('test_binary'))
    return cls(test_binary, **json_data)

  def readable_info(self):
    """Returns Human readable instruction of the reproducing steps."""
    if not self.reproducing_rate:
      return 'This failure was NOT reproduced by {0} strategy.'.format(
        self.strategy
      )
    message = []
    message.append(
      'The failure could be reproduced ({0:.1f}%) with command'.format(
        self.reproducing_rate * 100
      )
    )
    message.append(' by {0} strategy:\n\n'.format(self.strategy))
    message.append(self.test_binary.readable_command())
    return ''.join(message)

  def better_than(self, other):
    """Returns if this reproducing step works better than the other."""
    # Reproducible step is always better than not reproduced step.
    if min(self.reproducing_rate, other.reproducing_rate) == 0.0:
      return self.reproducing_rate > other.reproducing_rate
    # Prefer reliable step (only apply if < 3 reproduction).
    if self.reproduced_cnt != other.reproduced_cnt and (
      self.reproduced_cnt < 3 or other.reproduced_cnt < 3
    ):
      return self.reproduced_cnt > other.reproduced_cnt
    # If the two reproducing step have similar reproducing_rate (>= 0.9), the
    # lesser tests is better. (It's not using the duration here because parallel
    # strategy would run a lot of tests in parallel which could have a shorter
    # duration than running the same number of repeats.)
    if (
      self.reproducing_rate >= 0.9
      and other.reproducing_rate >= 0.9
      and abs(self.reproducing_rate - other.reproducing_rate) < 0.03
    ):
      return len(self.test_binary.tests) < len(other.test_binary.tests)
    return self.reproducing_rate > other.reproducing_rate
