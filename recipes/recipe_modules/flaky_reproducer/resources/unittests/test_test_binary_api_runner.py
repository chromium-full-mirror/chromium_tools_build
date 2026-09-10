# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import json
import io
import unittest
from unittest.mock import patch

import test_binary_api_runner as api_runner
from testdata import get_test_path


class TestBinaryAPIRunnerTest(unittest.TestCase):
  @patch('sys.stdout', new_callable=io.StringIO)
  def test_create_test_binary_from_task_request(self, mock_stdout):
    api_runner.main(
      [
        'create_test_binary_from_task_request',
        '--task_request=' + get_test_path('gtest_task_request.json'),
      ],
      api_runner.methods,
    )
    data = json.loads(mock_stdout.getvalue())
    self.assertDictContainsSubset(
      {
        'class_name': 'GTestTestBinary',
        'command': [
          'vpython3',
          '../../testing/test_env.py',
          './base_unittests.exe',
          '--test-launcher-bot-mode',
          '--asan=0',
          '--lsan=0',
          '--msan=0',
          '--tsan=0',
          '--cfi-diag=0',
        ],
      },
      data,
    )

  @patch('sys.stdout', new_callable=io.StringIO)
  def test_get_test_binary_swarming_task_config(self, mock_stdout):
    api_runner.main(
      [
        'get_test_binary_swarming_task_config',
        '--test_binary=' + get_test_path('gtest_test_binary.json'),
      ],
      api_runner.methods,
    )
    data = json.loads(mock_stdout.getvalue())
    self.assertDictEqual(
      data,
      {
        'cas_input_root': 'b7c329e532e221e23809ba23f9af5b309aa17d490d845580207493d381998bd9/24',
        'cwd': 'out\\Release_x64',
        'dimensions': {
          'cpu': 'x86-64',
          'os': 'Windows-11-22000',
          'pool': 'chromium.tests',
        },
        'env_vars': {
          'ISOLATED_OUTDIR': '${ISOLATED_OUTDIR}',
          'LLVM_PROFILE_FILE': '${ISOLATED_OUTDIR}/profraw/default-%2m.profraw',
        },
      },
    )

  @patch('sys.stdout', new_callable=io.StringIO)
  def test_update_test_binary_with_reproducing_step(self, mock_stdout):
    api_runner.main(
      [
        'update_test_binary_with_reproducing_step',
        '--test_binary=' + get_test_path('gtest_test_binary.json'),
        '--reproducing_step=' + get_test_path('reproducing_step.json'),
      ],
      api_runner.methods,
    )
    data = json.loads(mock_stdout.getvalue())
    self.assertDictContainsSubset(
      {
        'class_name': 'GTestTestBinary',
        'tests': ["MockUnitTests.CrashTest", "MockUnitTests.PassTest"],
        'repeat': 1,
      },
      data,
    )

  @patch('sys.stdout', new_callable=io.StringIO)
  def test_test_binary_as_command(self, mock_stdout):
    api_runner.main(
      [
        'test_binary_as_command',
        '--test_binary=' + get_test_path('gtest_test_binary.json'),
        '--output=abc/',
      ],
      api_runner.methods,
    )
    data = json.loads(mock_stdout.getvalue())
    self.assertIn('./base_unittests.exe', data)
    self.assertIn('--test-launcher-summary-output=abc/', data)
