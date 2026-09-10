# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import re

from . import utils
from .base_test_binary import (
  BaseTestBinary,
  TestBinaryWithBatchMixin,
  TestBinaryWithParallelMixin,
)
from ..result_summary.blink_web_tests_result_summary import (
  BlinkWebTestsResultSummary,
)


class BlinkWebTestsBinary(
  TestBinaryWithBatchMixin, TestBinaryWithParallelMixin, BaseTestBinary
):
  RESULT_SUMMARY_CLS = BlinkWebTestsResultSummary

  def strip_for_bots(self):
    ret = super().strip_for_bots()

    # We will take control of these switches
    strip_switches = [
      'isolated-script-test-output',
      'write-full-results-to',
      'json-test-results',
      'json-failing-test-results',
      'results-directory',
      'isolated-script-test-perf-output',
      'num-retries',
      'test-launcher-retry-limit',
      'isolated-script-test-launcher-retry-limit',
      'no-retry-failures',
      'child-processes',
      'jobs',
      'j',
      'iterations',
      'isolated-script-test-repeat',
      'gtest_repeat',
      'isolated-script-test-also-run-disabled-tests',
      'restart-shell-between-tests',
      'test-list',
      'order',
      'seed',
      'isolated-script-test-filter-file',
      'test-launcher-filter-file',
      'isolated-script-test-filter',
      'gtest_filter',
      'gerrit-issue',
      'gerrit-patchset',
      'buildbucket-id',
      'git-revision',
      'write-run-histories-to',
    ]
    strip_switches = {key: 1 for key in strip_switches}
    ret.command = utils.strip_command_switches(ret.command, strip_switches)
    # Should strip all wrappers
    is_local = ret.command and re.match(r"^bin[\\\/]run_", ret.command[0])
    if not is_local:
      raise NotImplementedError(
        'Command line contains unknown wrapper: {0}'.format(ret.command)
      )

    shard_env_keys = ('GTEST_SHARD_INDEX', 'GTEST_TOTAL_SHARDS')
    ret.env_vars = utils.strip_env_vars(ret.env_vars, shard_env_keys)

    return ret

  def _get_command(self, filter_file=None, output_json=None):
    cmd = self.command[:]
    cmd.append('--test-launcher-retry-limit=0')  # Disable retry.
    # blink_web_tests will adjust the batch size based on --repeat_each or
    # --iterations, which might behave differently for strategies.
    # The workaround is to set --restart-shell-between-tests=never, which have
    # higher priority over other options and keep tests running in a same
    # content_shell.
    # And it's reasonable not to restart content_shell, as the runs are normally
    # irrelevant after content_shell restart.
    cmd.append('--restart-shell-between-tests=never')
    # blink_web_tests will shuffle the tests AFTER filtering. Which will
    # generate different execution order when applying filters.
    # The workaround here is to set --order=none that use user specified order.
    # see third_party/blink/tools/blinkpy/web_tests/controllers/manager.py:run
    cmd.append('--order=none')
    if filter_file:
      cmd.append("--test-launcher-filter-file={0}".format(filter_file))
    elif self.tests:
      # Only tests set via --gtest_filter support restore_order.
      # See following implementation:
      # blinkpy/web_tests/controllers/manager.py:_restore_order
      # blinkpy/web_tests/controllers/web_test_finder.py:find_tests
      # blinkpy/web_tests/run_web_tests.py:_set_up_derived_options
      cmd.append("--gtest_filter={0}".format(':'.join(self.tests)))
    if self.repeat:
      cmd.append("--isolated-script-test-repeat={0}".format(self.repeat))
    if self.single_batch:
      if self.parallel_jobs and self.parallel_jobs != 1:
        raise Exception("Can't use multiple parallel_jobs with single_batch.")
      # restart-shell-between-tests=never have higher priority over repeat in
      # run_web_tests.py that it can be used with repeat.
      cmd.append("--child-processes=1")
    if self.parallel_jobs:
      cmd.append("--child-processes={0}".format(self.parallel_jobs))
    if output_json:
      cmd.append("--write-run-histories-to={0}".format(output_json))
    return cmd
