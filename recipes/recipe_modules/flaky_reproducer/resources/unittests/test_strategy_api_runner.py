# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import json
import io
import unittest
import typing
from unittest.mock import patch, create_autospec

import api_runner_common
import strategy_api_runner as api_runner
from testdata import get_test_path, get_test_data


class StrategyAPIRunnerTest(unittest.TestCase):
  @patch('sys.stdout', new_callable=io.StringIO)
  def test_choose_strategies(self, mock_stdout):
    api_runner.main(
      [
        'choose_strategies',
        '--test_binary=' + get_test_path('gtest_test_binary.json'),
        '--result_summary=' + get_test_path('gtest_good_output.json'),
        '--test_name=MockUnitTests.CrashTest',
      ],
      api_runner.methods,
    )
    data = json.loads(mock_stdout.getvalue())
    self.assertListEqual(data, ['batch', 'repeat'])

  @patch('sys.stdout', new_callable=io.StringIO)
  def test_choose_best_reproducing_step(self, mock_stdout):
    api_runner.main(
      [
        'choose_best_reproducing_step',
        '--reproducing_steps',
        get_test_path('reproducing_step.json'),
        get_test_path('reproducing_step.json'),
      ],
      api_runner.methods,
    )
    data = json.loads(mock_stdout.getvalue())
    self.assertDictEqual(
      data, json.loads(get_test_data('reproducing_step.json'))
    )

  @patch('sys.stdout', new_callable=io.StringIO)
  def test_count_reproduced_failures(self, mock_stdout):
    api_runner.main(
      [
        'count_reproduced_failures',
        '--test_name=MockUnitTests.CrashTest',
        '--result_summaries',
        get_test_path('gtest_good_output.json'),
        get_test_path('gtest_good_output.json'),
        '--original_result_summary',
        get_test_path('gtest_good_output.json'),
      ],
      api_runner.methods,
    )
    data = json.loads(mock_stdout.getvalue())
    self.assertEqual(2, data)

  @patch('sys.stdout', new_callable=io.StringIO)
  @patch.dict(
    api_runner.methods,
    {
      'summarize_reproducing_steps': create_autospec(
        api_runner.summarize_reproducing_steps, return_value=()
      )
    },
  )
  def test_main_summarize_reproducing_steps(self, mock_stdout):
    # Patch the mock so that parse_args works.
    setattr(
      api_runner.methods['summarize_reproducing_steps'],
      '__annotations__',
      typing.get_type_hints(api_runner.summarize_reproducing_steps),
    )
    api_runner.main(
      [
        'summarize_reproducing_steps',
        '--reproducing_step',
        get_test_path('reproducing_step.json'),
        '--all_reproducing_steps',
        get_test_path('reproducing_step.json'),
      ],
      api_runner.methods,
    )
    # mocked method
    # pylint: disable-next=no-member
    api_runner.methods['summarize_reproducing_steps'].assert_called()

  def test_summarize_reproducing_steps(self):
    reproducing_step = api_runner_common.type_ReproducingStep(
      get_test_path('reproducing_step.json')
    )
    not_reproduced_step = api_runner_common.type_ReproducingStep(
      get_test_path('reproducing_step.json')
    )
    not_reproduced_step.debug_info['task_ui_link'] = "http://example.org/task"
    not_reproduced_step.reproducing_rate = 0
    not_reproduced_step.reproduced_cnt = 0

    header, message = api_runner.summarize_reproducing_steps(
      reproducing_step, [reproducing_step, not_reproduced_step]
    )
    self.assertEqual(
      header,
      (
        'The failure could be reproduced (90.0%) '
        'with command by repeat strategy:'
      ),
    )
    self.assertIn("It's verified with following strategies:", message)
    self.assertIn("repeat strategy reproduced 1 times (90.0%)", message)
    self.assertIn(") not reproduced", message)

    header, message = api_runner.summarize_reproducing_steps(
      not_reproduced_step, []
    )
    self.assertEqual(header, 'The failure could NOT be reproduced.')
