#!/usr/bin/env vpython3
# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import os
import re
import sys
import unittest

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(THIS_DIR, os.pardir)))

import constants


class ConstantsTest(unittest.TestCase):
  def test_excluded_sources_regex_matches(self):
    """Tests test files are matched by exclude pattern."""
    pattern = constants.EXCLUDED_FILE_REGEX
    files = [
      'a/b/c/test.cc',
      'a/b/c/tests.cc',
      'a/b/c/gtest.cc',
      'a/b/c/gtests.cc',
      'a/b/c/gTest.java',
      'a/b/c/gTests.java',
      'a/test/path.cc',
      'a/tests/path.cc',
      'a/b/testing/path.cc',
    ]
    filtered_files = list(filter(lambda s: re.match(pattern, s), files))
    self.assertEqual(len(files), len(filtered_files))

  def test_excluded_sources_regex_does_not_match(self):
    """Tests files that are not matched by exclude pattern."""
    ios_pattern = constants.EXCLUDED_FILE_REGEX
    files = [
      '/b/s/w/ir/cache/builder/src/ios/chrome/browser/signin/'
      'signin_browser_state_info_updater.mm',
      '/b/s/w/ir/cache/builder/src/base/mac/scoped_sending_event.mm',
      '/b/s/w/ir/cache/builder/src/contest_related/file.cc',
    ]
    filtered_files = list(filter(lambda s: re.match(ios_pattern, s), files))
    self.assertEqual(0, len(filtered_files))

  def test_ios_unit_tests_pattern(self):
    """Tests correct targets are matched by ios unit test pattern."""
    ios_unit_test_target_pattern = (
      constants.PLATFORM_TO_TARGET_NAME_PATTERN_MAP['ios']['unit']
    )

    unit_test_targets = [
      'boringssl_crypto_tests',
      'boringssl_ssl_tests',
      'crypto_unittests',
      'google_apis_unittests',
      'ios_components_unittests',
      'ios_net_unittests',
      'ios_remoting_unittests',
      'ios_testing_unittests',
      'net_unittests',
      'services_unittests',
      'sql_unittests',
      'url_unittests',
      'base_unittests',
      'components_unittests',
      'gfx_unittests',
      'ios_chrome_unittests',
      'ios_web_unittests',
      'ios_web_view_unittests',
      'skia_unittests',
      'ui_base_unittests',
    ]
    filtered_unit_tests = list(
      filter(
        lambda s: re.match(ios_unit_test_target_pattern, s), unit_test_targets
      )
    )
    self.assertEqual(len(unit_test_targets), len(filtered_unit_tests))

    non_unit_test_targets = [
      'ios_web_inttests',
      'ios_web_view_inttests',
      'ios_chrome_bookmarks_eg2tests_module',
      'ios_chrome_integration_eg2tests_module',
      'ios_chrome_settings_eg2tests_module',
      'ios_chrome_signin_eg2tests_module',
      'ios_chrome_smoke_eg2tests_module',
      'ios_chrome_ui_eg2tests_module',
      'ios_chrome_web_eg2tests_module',
      'ios_showcase_eg2tests_module',
      'ios_web_shell_eg2tests_module',
    ]
    filtered_non_unit_tests = list(
      filter(
        lambda s: re.match(ios_unit_test_target_pattern, s),
        non_unit_test_targets,
      )
    )
    self.assertEqual(0, len(filtered_non_unit_tests))

  def test_linux_unit_tests_pattern(self):
    """Tests correct targets are matched by linux unit test pattern.
    Only a few representative test suites are tested for.
    """
    linux_unit_test_target_pattern = (
      constants.PLATFORM_TO_TARGET_NAME_PATTERN_MAP['linux']['unit']
    )

    unit_test_targets = [
      'absl_hardening_tests',
      'accessibility_unittests',
      'boringssl_crypto_tests',
      'boringssl_ssl_tests',
      'compositor_unittests',
      'content_shell_crash_test',
      'gpu_unittests',
      'unit_tests',
    ]
    filtered_unit_tests = list(
      filter(
        lambda s: re.match(linux_unit_test_target_pattern, s), unit_test_targets
      )
    )
    self.assertEqual(len(unit_test_targets), len(filtered_unit_tests))

    non_unit_test_targets = [
      'blink_web_tests',
      'browser_tests',
      'components_browsertests',
      'sync_integration_tests',
      'webdriver_wpt_tests',
    ]
    filtered_non_unit_tests = list(
      filter(
        lambda s: re.match(linux_unit_test_target_pattern, s),
        non_unit_test_targets,
      )
    )
    self.assertEqual(0, len(filtered_non_unit_tests))

  def test_mac_unit_tests_pattern(self):
    """Tests correct targets are matched by mac unit test pattern.
    Only a few representative test suites are tested for.
    """
    mac_unit_test_target_pattern = (
      constants.PLATFORM_TO_TARGET_NAME_PATTERN_MAP['mac']['unit']
    )

    unit_test_targets = [
      'absl_hardening_tests',
      'boringssl_crypto_tests',
      'boringssl_ssl_tests',
      'crashpad_tests',
      'cronet_tests',
      'ipc_tests',
      'crypto_unittests',
      'perfetto_unittests',
      'unit_tests',
    ]
    filtered_unit_tests = list(
      filter(
        lambda s: re.match(mac_unit_test_target_pattern, s), unit_test_targets
      )
    )
    self.assertEqual(len(unit_test_targets), len(filtered_unit_tests))

    non_unit_test_targets = [
      'browser_tests',
      'content_browsertests',
      'components_browsertests',
      'headless_browsertests',
      'sync_integration_tests',
    ]
    filtered_non_unit_tests = list(
      filter(
        lambda s: re.match(mac_unit_test_target_pattern, s),
        non_unit_test_targets,
      )
    )
    self.assertEqual(0, len(filtered_non_unit_tests))

  def test_win_unit_tests_pattern(self):
    """Tests correct targets are matched by win unit test pattern.
    Only a few representative test suites are tested for.
    """
    win_unit_test_target_pattern = (
      constants.PLATFORM_TO_TARGET_NAME_PATTERN_MAP['win']['unit']
    )

    unit_test_targets = [
      'absl_hardening_tests',
      'boringssl_crypto_tests',
      'boringssl_ssl_tests',
      'crashpad_tests',
      'cronet_tests',
      'ipc_tests',
      'vr_pixeltests',
      'perfetto_unittests',
      'unit_tests',
    ]
    filtered_unit_tests = list(
      filter(
        lambda s: re.match(win_unit_test_target_pattern, s), unit_test_targets
      )
    )
    self.assertEqual(len(unit_test_targets), len(filtered_unit_tests))

    non_unit_test_targets = [
      'browser_tests',
      'content_browsertests',
      'components_browsertests',
      'headless_browsertests',
      'sync_integration_tests',
    ]
    filtered_non_unit_tests = list(
      filter(
        lambda s: re.match(win_unit_test_target_pattern, s),
        non_unit_test_targets,
      )
    )
    self.assertEqual(0, len(filtered_non_unit_tests))

  def test_chromeos_unit_tests_pattern(self):
    """Tests correct targets are matched by chromeos unit test pattern.
    Only a few representative test suites are tested for.
    """
    win_unit_test_target_pattern = (
      constants.PLATFORM_TO_TARGET_NAME_PATTERN_MAP['chromeos']['unit']
    )

    unit_test_targets = ['crashpad_tests', 'ipc_tests', 'unit_tests']
    filtered_unit_tests = list(
      filter(
        lambda s: re.match(win_unit_test_target_pattern, s), unit_test_targets
      )
    )
    self.assertEqual(len(unit_test_targets), len(filtered_unit_tests))

    non_unit_test_targets = [
      'browser_tests',
      'content_browsertests',
      'components_browsertests',
      'headless_browsertests',
      'sync_integration_tests',
    ]
    filtered_non_unit_tests = list(
      filter(
        lambda s: re.match(win_unit_test_target_pattern, s),
        non_unit_test_targets,
      )
    )
    self.assertEqual(0, len(filtered_non_unit_tests))


if __name__ == '__main__':
  unittest.main()
