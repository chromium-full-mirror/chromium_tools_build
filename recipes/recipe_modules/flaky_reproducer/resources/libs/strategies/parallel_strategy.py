# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import collections
import math
import logging
import os
import time

from . import utils
from .base_strategy import BaseStrategy
from ..test_binary import TestBinaryWithParallelMixin


class ReproduceTestResult:
  """Data container for ParallelStrategy._verify_parallel_tests results."""

  __slots__ = ['tests', 'reproduced', 'test_binary', 'test_history']

  def __init__(self, tests, reproduced, test_binary, test_history):
    self.tests = tests
    self.reproduced = reproduced
    self.test_binary = test_binary
    self.test_history = test_history


class ParallelStrategy(BaseStrategy):
  name = 'parallel'

  # Parallel multiples to increase the load.
  PARALLEL_JOBS_MULTIPLES = 1.5
  # Repeats for single iteration.
  MAX_REPEAT = 10
  MIN_REPEAT = 5
  # Max iterations before reproduced 3 times.
  MAX_ITERATIONS = 10
  # We are expecting 3 reproduction in iterations.
  REPRODUCE_CNT = 3
  # Ignore the groups not reproduced after NOT_REPRODUCE_RETRY.
  NOT_REPRODUCE_RETRY = 2
  # Time limits.
  SINGLE_ITERATION_TIME_LIMIT = 5 * 60
  GROUP_VERIFY_TIME_LIMIT = 20 * 60
  SINGLE_TEST_VERIFY_TIME_LIMIT = 10 * 60

  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self.failing_sample = None
    self.deadline = None
    self.parallel_jobs = None
    self.running_time = {}  # test_name: [total_duration, run_cnt]

  def valid_for_test(self):
    if not isinstance(self.test_binary, TestBinaryWithParallelMixin):
      return False
    self.failing_sample = self.result_summary.get_failing_sample(
      self.test_name, default=None
    )
    if (
      not self.failing_sample  #
      or not self.failing_sample.start_time
      or self.failing_sample.duration is None
    ):
      return False
    parallel_tests = self._get_parallel_tests()
    if not parallel_tests:
      return False
    return True

  def run(self, timeout=45 * 60):
    self.deadline = time.time() + timeout

    self.failing_sample = self.result_summary.get_failing_sample(
      self.test_name, default=None
    )
    if (
      not self.failing_sample  #
      or not self.failing_sample.start_time
      or self.failing_sample.duration is None
    ):
      raise Exception(
        'Parallel strategy requires failing sample with timing info.'
      )
    logging.info(
      'Running %s strategy for %s with reason: %s',
      self.name,
      self.test_name,
      self.failing_sample.primary_error_message,
    )

    parallel_tests = self._get_parallel_tests()
    if not parallel_tests:
      return self._reproducing_step(self.test_binary, reproducing_rate=0)
    for test in parallel_tests:
      self.running_time[test.test_name] = [test.duration, 1]
    self.running_time[self.test_name] = [self.failing_sample.duration, 1]

    # Prefer parallel jobs as the number of threads when test fails.
    self.parallel_jobs = len(
      set(
        [x.thread_id for x in parallel_tests]  #
        + [self.failing_sample.thread_id]
      )
    )
    # Or the current cpu count.
    if self.parallel_jobs <= 1:
      self.parallel_jobs = max(os.cpu_count(), 2)
    # Multiply the parallel jobs to increase the pressure that could improve the
    # reproducing rate.
    self.parallel_jobs = math.floor(
      self.parallel_jobs * self.PARALLEL_JOBS_MULTIPLES
    )

    return self._find_best_parallel_tests(parallel_tests)

  def _find_best_parallel_tests(self, tests):
    # Ideally, we want to repeat a group of tests running in parallel while
    # always have the failing test running in another thread. But as that is not
    # supported by test harness.
    # Instead, we would repeat |parallel_jobs| tasks in parallel, which would
    # guarantee a thread runs the failing tests. Plus, we assume the failure
    # just need 1 other test run in parallel to reproduce.

    # Step 1: Verify reproducibility in groups.
    # Group size = parallel_jobs - 1 slot reversed for failing sample.
    groups = [
      tests[i : i + self.parallel_jobs - 1]
      for i in range(0, len(tests), self.parallel_jobs - 1)
    ]
    best_group = self._find_best_parallel_group(
      groups, self.GROUP_VERIFY_TIME_LIMIT
    )
    if not best_group:
      return self._reproducing_step(self.test_binary, reproducing_rate=0)
    best_group_step = self._generate_reproduce_step(best_group)

    # Step 2: Verify individual test.
    logging.info(
      'best_group.tests=%r, reproduced=%d, test_history=%d',
      [t.test_name for t in best_group.tests],
      best_group.reproduced,
      len(best_group.test_history),
    )
    best_test = self._find_best_parallel_group(
      [[t] for t in best_group.tests], self.SINGLE_TEST_VERIFY_TIME_LIMIT
    )
    if not best_test:
      return best_group_step
    best_test_step = self._generate_reproduce_step(best_test)
    if best_test_step.better_than(best_group_step):
      return best_test_step
    return best_group_step

  def _find_best_parallel_group(self, groups, time_limit):
    """Verify the reproduction of groups and return the best."""
    deadline = min(self.deadline, time.time() + time_limit)
    iteration = 0
    max_reproduced_cnt = 0
    group_test_results = {}  # {group_index: ReproduceTestResult}
    # Retry until reproduced REPRODUCE_CNT times or GROUP_VERIFY_TIME_LIMIT.
    while (
      iteration < self.MAX_ITERATIONS
      and max_reproduced_cnt < self.REPRODUCE_CNT
      and time.time() < deadline
    ):
      for i, group in enumerate(groups):
        # Ignore the groups that not reproducible after 2 retries (if we have
        # reproducible group).
        if (
          max_reproduced_cnt
          and iteration >= self.NOT_REPRODUCE_RETRY
          and (
            i not in group_test_results or group_test_results[i].reproduced == 0
          )
        ):
          continue
        ret = self._verify_parallel_tests(group)
        logging.info(
          'verify group %d/%d: iter=%d, reproduced=%d/%d',
          i + 1,
          len(groups),
          iteration,
          ret.reproduced,
          len(ret.test_history),
        )
        if i not in group_test_results:
          group_test_results[i] = ret
        else:
          group_test_results[i].reproduced += ret.reproduced
          group_test_results[i].test_history += ret.test_history
      iteration += 1
      max_reproduced_cnt = max(
        v.reproduced for v in group_test_results.values()
      )
    if not group_test_results:
      return None
    best_group_result = max(
      group_test_results.values(),
      key=lambda g: (g.reproduced, -len(g.test_history)),
    )
    if not best_group_result.reproduced:
      return None
    return best_group_result

  def _generate_reproduce_step(self, group_result):
    single_round_runtime = self._calc_single_round_runtime(
      group_result.tests + [self.failing_sample]
    )
    max_repeat = math.floor(
      self.TARGET_REPRODUCING_TIME_LIMIT * 1000 / single_round_runtime
    )
    failure_rate = (
      group_result.reproduced * 1.0 / len(group_result.test_history)
    )
    suggested_repeat = min(
      max_repeat,
      utils.calc_repeat_times_based_on_failing_rate(
        self.TARGET_REPRODUCING_RATE, failure_rate
      ),
    )
    reproducing_rate = 1.0 - (1.0 - failure_rate) ** suggested_repeat
    logging.debug(
      'generate_reproduce_step: single_round_runtime=%d, '
      'max_repeat=%d, failure_rate=%d, suggested_repeat=%d',
      single_round_runtime,
      max_repeat,
      failure_rate,
      suggested_repeat,
    )
    return self._reproducing_step(
      group_result.test_binary.with_repeat(suggested_repeat),
      reproducing_rate=reproducing_rate,
      duration=single_round_runtime * suggested_repeat,
      reproduced_cnt=group_result.reproduced,
      total_run_cnt=len(group_result.test_history),
    )

  def _verify_parallel_tests(self, tests, repeat=None):
    assert self.failing_sample
    if repeat is None:
      repeat = self._calc_single_round_repeat(tests + [self.failing_sample])
    test_binary = (
      self.test_binary.with_tests(  # go/pyformat-break
        [t.test_name for t in tests] + [self.test_name]
      )  #
      .with_parallel_jobs(len(tests) + 1)  #
      .with_repeat(repeat)  #
    )
    result = test_binary.run()
    for test in result:
      if test.duration is None:
        continue
      self.running_time[test.test_name][0] += test.duration
      self.running_time[test.test_name][1] += 1
    test_history = result.get_all(self.test_name)
    if not test_history:
      raise KeyError(
        "Target test wasn't executed during reproducing: {0}".format(
          self.test_name
        )
      )
    # Ignore the test results after the first failure.
    # As flaky reproducer, we care about the first reproduction. Tests could
    # fall into a bad state after first failure that cause false high
    # reproducing rate.
    filtered_test_history = []
    reproduced = 0
    for test in test_history:
      filtered_test_history.append(test)
      if self.failing_sample.similar_with(test):
        reproduced += 1
        break
    return ReproduceTestResult(
      tests, reproduced, test_binary, filtered_test_history
    )

  def _calc_single_round_runtime(self, tests, parallel_jobs=None):
    if parallel_jobs is None:
      parallel_jobs = len(tests)
    total_duration = 0
    for test in tests:
      if test.test_name not in self.running_time:
        continue
      duration, cnt = self.running_time[test.test_name]
      total_duration += duration / cnt
    if not total_duration:
      return self.MAX_REPEAT
    return total_duration / parallel_jobs

  def _calc_single_round_repeat(self, tests, parallel_jobs=None):
    repeat = math.floor(
      self.SINGLE_ITERATION_TIME_LIMIT
      * 1000
      / self._calc_single_round_runtime(tests, parallel_jobs)
    )
    return min(self.MAX_REPEAT, max(self.MIN_REPEAT, repeat))

  def _get_parallel_tests(self):
    """Find tests ran in parallel with failing sample."""
    assert self.failing_sample
    parallel_tests = []
    start_time = self.failing_sample.start_time
    end_time = start_time + self.failing_sample.duration / 1000
    for test in self.result_summary:
      if test is self.failing_sample:
        continue
      if (
        self.failing_sample.thread_id is not None
        and self.failing_sample.thread_id == test.thread_id
      ):
        continue
      if test.start_time is None or test.duration is None:
        continue
      if (
        test.start_time <= end_time
        and test.start_time + test.duration / 1000 >= start_time
      ):
        parallel_tests.append(test)
    return sorted(parallel_tests, key=lambda x: x.start_time or 0)
