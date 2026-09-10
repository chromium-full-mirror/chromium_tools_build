# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import io
import sys
import unittest
import typing
from unittest.mock import patch

import api_runner_common
from libs.test_binary import BaseTestBinary, TaskRequest
from libs.result_summary import BaseResultSummary
from libs.strategies import ReproducingStep
from testdata import get_test_path


class APIRunnerTest(unittest.TestCase):
  def setUp(self):
    pass

  def test_parse_args_with_task_request(self):

    def test_method(a: TaskRequest):
      pass

    args = api_runner_common.parse_args(
      [
        'test_method',
        '--a',
        get_test_path('gtest_task_request.json'),
      ],
      {'test_method': test_method},
    )
    self.assertIsInstance(args.a, TaskRequest)

  def test_parse_args_with_test_binary(self):

    def test_method(a: BaseTestBinary):
      pass

    args = api_runner_common.parse_args(
      [
        'test_method',
        '--a',
        get_test_path('gtest_test_binary.json'),
      ],
      {'test_method': test_method},
    )
    self.assertIsInstance(args.a, BaseTestBinary)

  def test_parse_args_with_result_summary(self):

    def test_method(a: BaseResultSummary):
      pass

    args = api_runner_common.parse_args(
      [
        'test_method',
        '--a',
        get_test_path('gtest_good_output.json'),
      ],
      {'test_method': test_method},
    )
    self.assertIsInstance(args.a, BaseResultSummary)

  def test_parse_args_with_reproducing_step(self):

    def test_method(a: ReproducingStep):
      pass

    args = api_runner_common.parse_args(
      [
        'test_method',
        '--a',
        get_test_path('reproducing_step.json'),
      ],
      {'test_method': test_method},
    )
    self.assertIsInstance(args.a, ReproducingStep)

  def test_parse_args_with_list(self):

    def test_method(a: typing.List[str]):
      pass

    args = api_runner_common.parse_args(
      ['test_method', '--a', 'asdf', 'fdsa'], {'test_method': test_method}
    )
    self.assertListEqual(args.a, ['asdf', 'fdsa'])

  @patch('sys.stderr', new_callable=io.StringIO)
  def test_parse_args_with_unknown_method(self, mock_stderr):
    with self.assertRaises(SystemExit):
      api_runner_common.parse_args(['unknown'], {})
    self.assertRegexpMatches(mock_stderr.getvalue(), r'invalid choice')

  @patch('sys.stderr', new_callable=io.StringIO)
  def test_parse_args_with_unknown_args(self, mock_stderr):

    def test_method():
      pass

    with self.assertRaises(SystemExit):
      api_runner_common.parse_args(
        [
          'test_method',
          '--unknown=asdf',
        ],
        {'test_method': test_method},
      )
    self.assertRegexpMatches(mock_stderr.getvalue(), r'unrecognized arguments')

  @patch('sys.stderr', new_callable=io.StringIO)
  def test_parse_args_with_missing_args(self, mock_stderr):

    def test_method(a: str):
      pass

    args = api_runner_common.parse_args(
      ['test_method'], {'test_method': test_method}
    )
    self.assertIsNone(args.a)

  @patch('sys.stderr', new_callable=io.StringIO)
  def test_parse_args_with_missing_files(self, mock_stderr):

    def test_method(a: BaseTestBinary):
      pass

    with self.assertRaises(FileNotFoundError):
      api_runner_common.parse_args(
        ['test_method', '--a', 'asdf'], {'test_method': test_method}
      )
