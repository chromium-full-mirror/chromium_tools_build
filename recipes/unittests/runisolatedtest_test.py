#!/usr/bin/env python3
# Copyright (c) 2013 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import json
import os
import shutil
import sys
import tempfile
import unittest

BASE_PATH = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(BASE_PATH)))

from recipes import runisolatedtest


class TestAll(unittest.TestCase):
  def setUp(self):
    super().setUp()
    self._run_command = runisolatedtest.run_command
    self.tempdir = tempfile.mkdtemp(prefix='runisolatedtest')

  def tearDown(self):
    runisolatedtest.run_command = self._run_command
    shutil.rmtree(self.tempdir)
    super().tearDown()

  def test_arguments(self):
    actual = []
    runisolatedtest.run_command = lambda x: actual.append(x) or 0
    exe = os.path.join(self.tempdir, 'foo')
    isolated = exe + '.isolate'

    data = {
      'version': '1.0',
      'command': ['../testing/test_env.py', r'..\out\Release/browser_test.exe'],
      'files': {r'out\Release\testdata': {}},
      'variables': {
        'EXECUTABLE_SUFFIX': '.exe',
        'OS': 'win',
        'PRODUCT_DIR': '../out/Release',
      },
    }
    with open(isolated, 'w') as f:
      json.dump(data, f)

    sample_line = [
      '--test_name',
      'base_unittests',
      '--builder_name',
      "Linux Tests",
      '--checkout_dir',
      'build/',
      exe,
      '--',
      '/usr/bin/python',
      'build/src/out/../tools/sharding_supervisor/sharding_supervisor.py',
      '--no-color',
      '--retry-failed',
      'build/src/out/Release/base_unittests',
      '--gtest_print_time',
      '--gtest_output=xml:build/gtest-results/base_unittests.xml',
      '--gtest_filter=Junk',
    ]
    expected = [
      [
        'build/src/tools/luci-go/isolate',
        'run',
        '-isolate',
        isolated,
        '-verbose',
        '--',
        '--no-cr',
        '--gtest_output=xml:build/gtest-results/base_unittests.xml',
        '--gtest_filter=Junk',
      ],
    ]
    res = runisolatedtest.main(sample_line)

    expected_data = {
      'version': '1.0',
      'command': ['../testing/test_env.py', r'..\out\Release/browser_test.exe'],
      'files': {r'out\Release\testdata': {}},
      'variables': {
        'EXECUTABLE_SUFFIX': '.exe',
        'OS': 'win',
        'PRODUCT_DIR': '../out/Release',
      },
    }
    with open(isolated) as f:
      converted_data = json.load(f)

    self.assertEqual(expected_data, converted_data)

    self.assertEqual(0, res)
    self.assertEqual(expected, actual)


if __name__ == '__main__':
  unittest.main()
