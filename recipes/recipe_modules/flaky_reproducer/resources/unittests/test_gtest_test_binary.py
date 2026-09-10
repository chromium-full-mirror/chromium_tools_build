# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import json
import unittest

from libs.test_binary.gtest_test_binary import GTestTestBinary
from testdata import get_test_data


class GTestTestBinaryTest(unittest.TestCase):
  def setUp(self):
    jsonish = json.loads(get_test_data('gtest_test_binary.json'))
    self.test_binary = GTestTestBinary.from_jsonish(jsonish)

  def test_strip_for_bots(self):
    test_binary = self.test_binary.strip_for_bots()
    self.assertEqual(
      test_binary.command,
      [
        "vpython3",
        "../../testing/test_env.py",
        "./base_unittests.exe",
        "--test-launcher-bot-mode",
        "--asan=0",
        "--lsan=0",
        "--msan=0",
        "--tsan=0",
        "--cfi-diag=0",
      ],
    )
    self.assertNotIn('LLVM_PROFILE_FILE', test_binary.env_vars)

    # should strip GTEST_STRIP_SWITCHES with it's value
    test_binary = GTestTestBinary(
      [
        "./unittests",
        "--gtest_filter=AAA.bbb",
        # should be kept as it's a switch named "gtest_filte AAA.bbb"
        "--gtest_filte AAA.bbb",
        "--isolated-script-test-filter",
        "AAA.bbb",
        "--isolated-script-test-repeat",
        # should be kept as it's not an value of switch
        "--gtest_also_run_disabled_tests",
        "--test-launcher-filter-file",
      ]
    )
    self.assertEqual(
      test_binary.strip_for_bots().command,
      [
        "./unittests",
        "--gtest_filte AAA.bbb",
        "--gtest_also_run_disabled_tests",
      ],
    )

    test_binary = GTestTestBinary(['rdb', '--', 'echo', '123'])
    with self.assertRaisesRegex(
      NotImplementedError, 'Command line contains unknown wrapper:'
    ):
      test_binary.strip_for_bots()

  def test_strip_for_bots_for_wrappers(self):
    # The following commands shouldn't raise NotImplementedError
    GTestTestBinary(["bin/run_with_asan"]).strip_for_bots()
    GTestTestBinary(["bin/run_interactive_ui_tests"]).strip_for_bots()
    GTestTestBinary(["bin\\run_blink_web_tests.bat"]).strip_for_bots()

  def test_get_command(self):
    test_binary = self.test_binary.strip_for_bots()
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
        'vpython3',
        '../../testing/test_env.py',
        './base_unittests.exe',
        '--test-launcher-bot-mode',
        '--asan=0',
        '--lsan=0',
        '--msan=0',
        '--tsan=0',
        '--cfi-diag=0',
        '--test-launcher-retry-limit=0',
        '--gtest_filter=MockUnitTests.CrashTest:MockUnitTests.CrashTest',
        '--gtest_repeat=1',
        '--test-launcher-batch-limit=0',
        '--test-launcher-jobs=1',
      ],
    )

  def test_get_command_with_filter_file_and_output(self):
    test_binary = self.test_binary.strip_for_bots()
    command = test_binary._get_command('test-filter-file', 'test-output')
    self.assertEqual(
      command,
      [
        'vpython3',
        '../../testing/test_env.py',
        './base_unittests.exe',
        '--test-launcher-bot-mode',
        '--asan=0',
        '--lsan=0',
        '--msan=0',
        '--tsan=0',
        '--cfi-diag=0',
        '--test-launcher-retry-limit=0',
        '--test-launcher-filter-file=test-filter-file',
        '--test-launcher-summary-output=test-output',
      ],
    )

  def test_with_single_batch_with_repeat_raise_error(self):
    self.test_binary.with_single_batch()._get_command()
    with self.assertRaisesRegex(
      Exception, "Can't repeat the tests with single batch in GTest."
    ):
      self.test_binary.with_single_batch().with_repeat(2)._get_command()
