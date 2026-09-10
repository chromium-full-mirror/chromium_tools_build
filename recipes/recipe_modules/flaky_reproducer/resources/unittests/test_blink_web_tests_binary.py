# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import json
import unittest

from libs.test_binary.blink_web_tests_binary import BlinkWebTestsBinary
from testdata import get_test_data


class BlinkWebTestsBinaryTest(unittest.TestCase):
  def test_strip_for_bots(self):
    test_binary = BlinkWebTestsBinary(
      [
        "rdb",
        "stream",
        "-test-id-prefix",
        "ninja://:blink_wpt_tests/",
        "-var",
        "builder:mac-rel",
        "-var",
        "os:Mac-12",
        "-var",
        "test_suite:blink_wpt_tests",
        "-tag",
        "step_name:blink_wpt_tests (with patch) on Mac-12",
        "-tag",
        "target_platform:mac",
        "-coerce-negative-duration",
        "-location-tags-file",
        "../../testing/location_tags.json",
        "-exonerate-unexpected-pass",
        "--",
        "luci-auth",
        "context",
        "--",
        "bin/run_blink_wpt_tests",
        "--results-directory",
        "${ISOLATED_OUTDIR}",
        "--isolated-script-test-output=${ISOLATED_OUTDIR}/output.json",
        (
          "--isolated-script-test-perf-output="
          "${ISOLATED_OUTDIR}/perftest-output.json"
        ),
        "--num-retries=3",
        "--write-run-histories-to=${ISOLATED_OUTDIR}/run_histories.json",
        "--git-revision=ffffffffffffffffffffffffffffffffffffffff",
        "--gerrit-issue=1111111",
        "--gerrit-patchset=1",
        "--buildbucket-id=1111111111111111111",
      ]
    )
    test_binary = test_binary.strip_for_bots()
    self.assertEqual(test_binary.command, ['bin/run_blink_wpt_tests'])

    test_binary = BlinkWebTestsBinary(['rdb', '--', 'echo', '123'])
    with self.assertRaisesRegex(
      NotImplementedError, 'Command line contains unknown wrapper:'
    ):
      test_binary.strip_for_bots()

  def test_strip_for_bots_for_wrappers(self):
    # The following commands shouldn't raise NotImplementedError
    BlinkWebTestsBinary(["bin/run_with_asan"]).strip_for_bots()
    BlinkWebTestsBinary(["bin/run_interactive_ui_tests"]).strip_for_bots()
    BlinkWebTestsBinary(["bin\\run_blink_web_tests.bat"]).strip_for_bots()

  def test_get_command(self):
    jsonish = json.loads(get_test_data('blink_web_tests_binary.json'))
    test_binary = BlinkWebTestsBinary.from_jsonish(jsonish)
    self.assertIsNotNone(test_binary.RESULT_SUMMARY_CLS)

    command = (
      test_binary.with_tests(['MockUnitTests.CrashTest'] * 2)
      .with_repeat(1)
      .with_single_batch()
      .with_parallel_jobs(1)
      ._get_command()
    )
    self.assertEqual(
      command,
      [
        'bin/run_blink_wpt_tests',
        '--test-launcher-retry-limit=0',
        '--restart-shell-between-tests=never',
        '--order=none',
        '--gtest_filter=MockUnitTests.CrashTest:MockUnitTests.CrashTest',
        '--isolated-script-test-repeat=1',
        '--child-processes=1',
        '--child-processes=1',
      ],
    )

  def test_get_command_with_filter_file_and_output(self):
    jsonish = json.loads(get_test_data('blink_web_tests_binary.json'))
    test_binary = BlinkWebTestsBinary.from_jsonish(jsonish)
    command = test_binary._get_command('test-fitler-file', 'test-output')
    self.assertEqual(
      command,
      [
        'bin/run_blink_wpt_tests',
        '--test-launcher-retry-limit=0',
        '--restart-shell-between-tests=never',
        '--order=none',
        '--test-launcher-filter-file=test-fitler-file',
        '--write-run-histories-to=test-output',
      ],
    )

  def test_single_batch_with_parallel(self):
    jsonish = json.loads(get_test_data('blink_web_tests_binary.json'))
    test_binary = BlinkWebTestsBinary.from_jsonish(jsonish)
    with self.assertRaisesRegex(
      Exception, "Can't use multiple parallel_jobs with single_batch."
    ):
      test_binary.with_single_batch().with_parallel_jobs(2)._get_command()
