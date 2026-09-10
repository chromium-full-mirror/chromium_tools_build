#!/usr/bin/env vpython3
# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

import mock
import ntpath

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(
  0, os.path.abspath(os.path.join(_THIS_DIR, os.pardir, 'resources'))
)

import write_paths_to_instrument


class WritePathsTest(unittest.TestCase):
  def test_basic(self):
    mock_open = mock.mock_open()
    with mock.patch('write_paths_to_instrument.open', mock_open, create=True):
      mock_argv = [
        'script',
        '--write-to',
        '/mock/path.txt',
        '--src-path',
        '/b/c/src',
        '--build-path',
        '/b/c/src/out/Release',
        'sub_dir_a/source_a.cc',
        'sub_dir_b/source_b.cc',
      ]
      with mock.patch('sys.argv', mock_argv):
        write_paths_to_instrument.main()
      self.assertEqual(mock.call('/mock/path.txt', 'w'), mock_open.call_args)
      mock_file = mock_open()
      self.assertEqual(
        mock.call('../../sub_dir_a/source_a.cc\n../../sub_dir_b/source_b.cc\n'),
        mock_file.write.call_args,
      )

  def test_windows(self):
    with (
      mock.patch('platform.system', mock.MagicMock(return_value='Windows')),
      mock.patch('os.path', ntpath),
    ):
      mock_open = mock.mock_open()
      with mock.patch('write_paths_to_instrument.open', mock_open, create=True):
        mock_argv = [
          'script',
          '--write-to',
          r'C:\mock\path.txt',
          '--src-path',
          r'C:\b\c\src',
          '--build-path',
          r'C:\b\c\src\out\Release',
          r'sub_dir_a\source_a.cc',
          r'sub_dir_b\source_b.cc',
        ]
        with mock.patch('sys.argv', mock_argv):
          write_paths_to_instrument.main()
        self.assertEqual(
          mock.call(r'C:\mock\path.txt', 'w'), mock_open.call_args
        )
        mock_file = mock_open()
        self.assertEqual(
          mock.call(
            '..\\..\\sub_dir_a\\source_a.cc\n..\\..\\sub_dir_b\\source_b.cc\n'
          ),
          mock_file.write.call_args,
        )


if __name__ == '__main__':
  unittest.main()
