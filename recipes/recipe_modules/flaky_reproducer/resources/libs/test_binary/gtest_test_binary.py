# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import os
import re

from . import utils
from .base_test_binary import (
  BaseTestBinary,
  TestBinaryWithBatchMixin,
  TestBinaryWithParallelMixin,
)
from ..result_summary.gtest_result_summary import GTestTestResultSummary


class GTestTestBinary(
  TestBinaryWithBatchMixin, TestBinaryWithParallelMixin, BaseTestBinary
):
  RESULT_SUMMARY_CLS = GTestTestResultSummary

  def strip_for_bots(self):
    ret = super().strip_for_bots()

    gtest_strip_switches = {
      key: 1
      for key in (
        'test-launcher-summary-output',
        'test-launcher-retry-limit',
        'isolated-script-test-repeat',
        'test-launcher-batch-limit',
        'test-launcher-jobs',
        'test-launcher-filter-file',
        'isolated-script-test-filter',
        'gtest_filter',
        'gtest-filter',
        # removed for chromeos/test_runner.py
        # The wrapper requires --test-launcher-summary-output and --logs-dir
        # point to the same directory
        'logs-dir',
        # removed for weblayer_instrumentation_test_versions.py
        # The wrapper will append --test-filter to the test launcher, which
        # conflict with --isolated-script-test-filter.
        'test-expectations',
      )
    }
    ret.command = utils.strip_command_switches(
      ret.command, gtest_strip_switches
    )
    # Should strip all wrappers
    is_local = ret.command and re.match(r'^(\.|bin[\\\/]run_)', ret.command[0])
    known_wrappers = set(('test_env.py', 'xvfb.py', 'logdog_wrapper.py'))
    is_test_env_py = (
      len(ret.command) >= 2
      and os.path.basename(ret.command[1]) in known_wrappers
    )
    if not (is_local or is_test_env_py):
      raise NotImplementedError(
        'Command line contains unknown wrapper: {0}'.format(ret.command)
      )

    gtest_env_keys = ('GTEST_SHARD_INDEX', 'GTEST_TOTAL_SHARDS')
    ret.env_vars = utils.strip_env_vars(ret.env_vars, gtest_env_keys)

    return ret

  def _get_command(self, filter_file=None, output_json=None):
    """Get the command line with switches applied."""
    cmd = self.command[:]
    cmd.append('--test-launcher-retry-limit=0')  # Disable retry.
    if filter_file:
      cmd.append("--test-launcher-filter-file={0}".format(filter_file))
    elif self.tests:
      cmd.append("--gtest_filter={0}".format(':'.join(self.tests)))
    if self.repeat:
      cmd.append("--gtest_repeat={0}".format(self.repeat))
    if self.single_batch:
      if self.repeat and self.repeat > 1:
        raise Exception(
          "Can't repeat the tests with single batch in GTest. See "
          "//base/test/launcher/test_launcher.cc"
        )
      cmd.append("--test-launcher-batch-limit=0")
    if self.parallel_jobs:
      cmd.append("--test-launcher-jobs={0}".format(self.parallel_jobs))
    if output_json:
      cmd.append("--test-launcher-summary-output={0}".format(output_json))
    return cmd
