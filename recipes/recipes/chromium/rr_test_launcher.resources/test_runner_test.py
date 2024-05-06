#!/usr/bin/env python3
# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import os
import sys
import unittest
from unittest.mock import patch, mock_open

THIS_DIR = os.path.dirname(__file__)

sys.path.insert(
    0,
    os.path.abspath(os.path.join(THIS_DIR, '..', 'rr_test_launcher.resources')))
import test_runner


class TestRunnerTest(unittest.TestCase):

  def test_args(self):
    args = ['--test=test1', '-t', 'test2', '--output-dir', 'output_dir']
    expected = ['test1', 'test2']
    res = test_runner.parse_args(args)
    self.assertEqual(res.test, expected)

    args = [
        '--test=test1', '-t', 'test2', '--output-dir', 'output_dir', '--',
        'test_binary', 'binary_arg1'
    ]
    expected_test = ['test1', 'test2']
    expected_test_cmd = ['test_binary', 'binary_arg1']
    res = test_runner.parse_args(args)
    self.assertEqual(res.test, expected_test)
    self.assertEqual(res.test_cmd, expected_test_cmd)

  @patch('subprocess.Popen')
  @patch('logging.info')
  @patch('os.chdir')
  def test_run_test_cmd(self, mock_chdir, mock_logging, mock_popen):
    mock_popen.return_value.wait.return_value = 0
    cmd = ['./exec', '--args']
    test_runner.run_cmd(cmd, cwd='out\\Release_x64')
    mock_logging.assert_called_once_with('Running %r in %r', cmd,
                                         'out\\Release_x64')
    mock_popen.assert_called_once_with(cmd)
    self.assertEqual(mock_chdir.call_count, 2)

  @patch('subprocess.Popen')
  @patch('logging.info')
  @patch('os.chdir')
  def test_run_with_failure_test(self, mock_chdir, mock_logging, mock_popen):
    mock_popen.return_value.__enter__.return_value.wait.return_value = 1
    cmd = ['./exec', '--args']
    ret = test_runner.run_cmd(cmd, cwd='out\\Release_x64')
    mock_popen.assert_called_once_with(cmd)
    self.assertEqual(ret, 1)
    self.assertEqual(mock_chdir.call_count, 2)

  def test_sanitize_test_name(self):
    test_name = r'~#%&*{}\:<>?/|"'
    expected = '_______________'
    self.assertEqual(test_runner.sanitize_test_name(test_name, '_'), expected)

    test_name = 'test.html'
    expected = 'test.html'
    self.assertEqual(test_runner.sanitize_test_name(test_name, '_'), expected)


if __name__ == '__main__':
  unittest.main()
