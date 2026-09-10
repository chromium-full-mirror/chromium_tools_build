# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import io
import os
import unittest
from unittest.mock import patch, mock_open

import strategy_runner
from testdata import get_test_data


class StrategyRunnerTest(unittest.TestCase):
  def setUp(self):
    # @patch('builtins.open', ...)
    mock_open_data = {
      'gtest_test_binary.json': get_test_data('gtest_test_binary.json'),
      'gtest_good_output.json': get_test_data('gtest_good_output.json'),
    }

    # pylint: disable=redefined-builtin
    def mock_open_method(file, *args, **kwargs):
      filename = os.path.basename(file)
      return mock_open(read_data=mock_open_data.get(filename, 'data'))()

    open_patcher = patch('builtins.open', new=mock_open_method)
    self.addClassCleanup(open_patcher.stop)
    open_patcher.start()

    # @patch('os.chdir')
    chdir_patcher = patch('os.chdir')
    self.addClassCleanup(chdir_patcher.stop)
    self.mock_chdir = chdir_patcher.start()

  @patch.object(
    strategy_runner, 'strategies', new={'repeat': None, 'batch': None}
  )
  @patch('sys.stderr', new_callable=io.StringIO)
  def test_parse_args(self, mock_stderr):
    args = strategy_runner.parse_args(
      ['repeat', '--test-binary=foo', '--result-summary=bar', 'foo.bar']
    )
    self.assertEqual(args.strategy, 'repeat')

    with self.assertRaises(SystemExit):
      strategy_runner.parse_args(['unknown'])
    self.assertRegexpMatches(mock_stderr.getvalue(), r'invalid choice')

    with self.assertRaises(SystemExit):
      strategy_runner.parse_args(['batch'])
    self.assertRegexpMatches(mock_stderr.getvalue(), r'arguments are required')

  @patch.object(strategy_runner.strategies['repeat'], 'run')
  @patch('sys.stdout', new_callable=io.StringIO)
  def test_main(self, mock_stdout, mock_strategy_run):
    mock_strategy_run.return_value.to_jsonish.return_value = {}
    mock_strategy_run.return_value.debug_info = {}
    strategy_runner.main(
      [
        'repeat',
        '--test-binary=gtest_test_binary.json',
        '--result-summary=gtest_good_output.json',
        'MockUnitTests.FailTest',
      ]
    )
    self.mock_chdir.assert_called()
    mock_strategy_run.assert_called()
    stdout = mock_stdout.getvalue()
    self.assertIn('REPRODUCING_STEP.READABLE_INFO:', stdout)
    self.assertIn('REPRODUCING_STEP.JSON:', stdout)

  @patch.object(strategy_runner.strategies['repeat'], 'run')
  @patch('sys.stdout', new_callable=io.StringIO)
  def test_main_with_output(self, mock_stdout, mock_strategy_run):
    mock_strategy_run.return_value.to_jsonish.return_value = {}
    mock_strategy_run.return_value.debug_info = {}
    strategy_runner.main(
      [
        'repeat',
        '--test-binary=gtest_test_binary.json',
        '--result-summary=gtest_good_output.json',
        '--output=reproducing_step.json',
        'MockUnitTests.FailTest',
      ]
    )
    mock_strategy_run.assert_called()
    stdout = mock_stdout.getvalue()
    self.assertIn('REPRODUCING_STEP.READABLE_INFO:', stdout)
    self.assertNotIn('REPRODUCING_STEP.JSON:', stdout)

  @patch.object(strategy_runner.strategies['repeat'], 'run')
  def test_main_test_not_found(self, mock_strategy_run):
    with self.assertRaises(LookupError):
      strategy_runner.main(
        [
          'repeat',
          '--test-binary=gtest_test_binary.json',
          '--result-summary=gtest_good_output.json',
          'NotExists.Test',
        ]
      )
