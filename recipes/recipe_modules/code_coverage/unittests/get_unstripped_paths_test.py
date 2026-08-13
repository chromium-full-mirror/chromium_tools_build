#!/usr/bin/env vpython3
# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import os
import sys
import unittest

import mock

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0,
                os.path.abspath(os.path.join(THIS_DIR, os.pardir, 'resources')))

import get_unstripped_paths


class GetUnstrippedPathsTest(unittest.TestCase):

  LIB_WALK = [
      ('/chromium/output/dir/lib.unstripped', [], [
          'libbase_unittests__library.so',
          'libandroid_browsertests__library.so',
          'libimmediate_crash_test_helper.so'
      ]),
  ]

  EXE_WALK = [
      ('/chromium/output/dir/exe.unstripped', ['obj'],
       ['md5sum_bin', 'test_child_process']),
      ('/chromium/output/dir/exe.unstripped/'
       'obj/third_party/breakpad/breakpad_unittests', [],
       ['breakpad_unittests']),
      ('/chromium/output/dir/exe.unstripped/'
       'obj/sandbox/linux/sandbox_linux_unittests', [],
       ['sandbox_linux_unittests']),
  ]

  EXEC_WALK = [
      ('/chromium/output/dir/exe.unstripped', [], [
          'base_unittests__exec', 'blink_common_unittests__exec',
          'blink_fuzzer_unittests__exec'
      ]),
  ]

  @mock.patch.object(os, 'walk')
  def test_get_all_paths(self, mock_walk):
    mock_walk.side_effect = [self.LIB_WALK, self.EXE_WALK]

    expected_output = [
        '/chromium/output/dir/lib.unstripped/libbase_unittests__library.so',
        '/chromium/output/dir/lib.unstripped/'
        'libandroid_browsertests__library.so',
        '/chromium/output/dir/lib.unstripped/'
        'libimmediate_crash_test_helper.so',
        '/chromium/output/dir/exe.unstripped/md5sum_bin',
        '/chromium/output/dir/exe.unstripped/test_child_process',
        '/chromium/output/dir/exe.unstripped/'
        'obj/third_party/breakpad/breakpad_unittests/breakpad_unittests',
        '/chromium/output/dir/exe.unstripped/'
        'obj/sandbox/linux/sandbox_linux_unittests/sandbox_linux_unittests',
    ]
    actual_output = get_unstripped_paths._get_all_paths('/chromium/output/dir')
    self.assertListEqual(expected_output, actual_output)

  @mock.patch.object(os, 'walk')
  def test_get_all_paths_exec(self, mock_walk):
    mock_walk.side_effect = [[], self.EXEC_WALK]

    expected_output = [
        '/chromium/output/dir/exe.unstripped/base_unittests__exec',
        '/chromium/output/dir/exe.unstripped/blink_common_unittests__exec',
        '/chromium/output/dir/exe.unstripped/blink_fuzzer_unittests__exec',
    ]
    actual_output = get_unstripped_paths._get_all_paths('/chromium/output/dir')
    self.assertListEqual(expected_output, actual_output)

  @mock.patch.object(os, 'walk')
  def test_get_all_paths_no_exe(self, mock_walk):
    mock_walk.side_effect = [self.LIB_WALK, []]

    expected_output = [
        '/chromium/output/dir/lib.unstripped/libbase_unittests__library.so',
        '/chromium/output/dir/lib.unstripped/'
        'libandroid_browsertests__library.so',
        '/chromium/output/dir/lib.unstripped/'
        'libimmediate_crash_test_helper.so',
    ]
    actual_output = get_unstripped_paths._get_all_paths('/chromium/output/dir')
    self.assertListEqual(expected_output, actual_output)

  @mock.patch.object(os, 'walk')
  def test_get_all_paths_no_lib(self, mock_walk):
    mock_walk.side_effect = [[], self.EXE_WALK]

    expected_output = [
        '/chromium/output/dir/exe.unstripped/md5sum_bin',
        '/chromium/output/dir/exe.unstripped/test_child_process',
        '/chromium/output/dir/exe.unstripped/'
        'obj/third_party/breakpad/breakpad_unittests/breakpad_unittests',
        '/chromium/output/dir/exe.unstripped/'
        'obj/sandbox/linux/sandbox_linux_unittests/sandbox_linux_unittests',
    ]
    actual_output = get_unstripped_paths._get_all_paths('/chromium/output/dir')
    self.assertListEqual(expected_output, actual_output)


if __name__ == '__main__':
  unittest.main()
