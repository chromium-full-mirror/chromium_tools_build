#!/usr/bin/env vpython3
# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import os
import sys
import unittest

import mock

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(
  0, os.path.abspath(os.path.join(THIS_DIR, os.pardir, 'resources'))
)

import get_native_libraries_for_android_target


class GetNativeLibrariesForAndroidTargetTest(unittest.TestCase):
  INPUT_WITH_WANTED_LIB = """
    {
      "variables": {
        "command": [
          "vpython3",
          "../../build/android/wrapper/test_wrapper.py",
          "--target",
          "some_target"
        ],
        "files": [
          "some_random_build_output_file",
          "some_random_folder/build_output_file",
          "./lib.unstripped/some_wanted_library_other_path_format.so",
          "lib.unstripped/some_other_with_other_suffix.a",
          "lib.unstripped/some_wanted_library.so",
          "../../third_party/some/lib.unstripped/some_library.so"
        ]
      }
    }
  """

  INPUT_WITHOUT_WANTED_LIB = """
      {
      "variables": {
        "command": [
          "vpython3",
          "../../build/android/wrapper/test_wrapper.py",
          "--target",
          "some_target"
        ],
        "files": [
          "some_random/build_output_file",
          "./exe.unstripped/some_wanted_library_other_path_format.so",
          "lib.unstripped/some_other_with_other_suffix.a",
          "exe.unstripped/some_wanted_library.so",
          "../../third_party/some/lib.unstripped/some_library.so"
        ]
      }
    }
  """

  def test_get_lib_paths(self):
    expected_output = [
      '/chromium/output/dir/lib.unstripped/'
      'some_wanted_library_other_path_format.so',
      '/chromium/output/dir/lib.unstripped/some_wanted_library.so',
    ]
    with mock.patch(
      'builtins.open', mock.mock_open(read_data=self.INPUT_WITH_WANTED_LIB)
    ) as m:
      actual_output = (
        get_native_libraries_for_android_target._get_library_paths(
          '/chromium/output/dir', 'target'
        )
      )
    m.assert_called_once_with('/chromium/output/dir/target.isolate')
    self.assertListEqual(expected_output, actual_output)

  def test_get_lib_paths_no_correct_libs(self):
    expected_output = []
    with mock.patch(
      'builtins.open', mock.mock_open(read_data=self.INPUT_WITHOUT_WANTED_LIB)
    ) as m:
      actual_output = (
        get_native_libraries_for_android_target._get_library_paths(
          '/chromium/output/dir', 'target'
        )
      )
    m.assert_called_once_with('/chromium/output/dir/target.isolate')
    self.assertListEqual(expected_output, actual_output)

  def test_error_if_file_non_exist(self):
    """The input targets should use isolate, and isolate file should exist."""
    with self.assertRaises(FileNotFoundError):
      get_native_libraries_for_android_target._get_library_paths(
        '/some/non/exist/path', 'any_target'
      )


if __name__ == '__main__':
  unittest.main()
