#!/usr/bin/env vpython3
# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

_SCRIPT = os.path.normpath(
  os.path.join(__file__, '..', '..', 'generate_groupings.py')
)


def _execute_generate_groupings(*args):
  cmd = [sys.executable, _SCRIPT] + list(args)
  subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)


class GenerateGroupingsIntegrationTest(unittest.TestCase):
  def test_generate_groupings(self):
    with tempfile.TemporaryDirectory() as d:
      try:
        _execute_generate_groupings('--groupings-dir', d, 'migration-testing')
      except subprocess.CalledProcessError as e:
        sys.stderr.write(e.output)
        raise

      with open(f'{d}/migration-testing.json') as f:
        data = json.load(f)
      self.maxDiff = None
      self.assertEqual(
        data,
        {
          'migration.testing:foo': {
            'builders': [
              'migration.testing:foo',
              'migration.testing:foo-x-tests',
              'migration.testing:foo-y-tests',
              'tryserver.migration.testing:foo',
            ],
          },
          'migration.testing:foo-x-tests': {
            'builders': [
              'migration.testing:foo',
              'migration.testing:foo-x-tests',
              'migration.testing:foo-y-tests',
              'tryserver.migration.testing:foo',
            ],
          },
          'migration.testing:foo-y-tests': {
            'builders': [
              'migration.testing:foo',
              'migration.testing:foo-x-tests',
              'migration.testing:foo-y-tests',
              'tryserver.migration.testing:foo',
            ],
          },
          'tryserver.migration.testing:foo': {
            'builders': [
              'migration.testing:foo',
              'migration.testing:foo-x-tests',
              'migration.testing:foo-y-tests',
              'tryserver.migration.testing:foo',
            ],
          },
          'migration.testing:bar': {
            'builders': [
              'migration.testing:bar',
              'migration.testing:bar-tests',
              'tryserver.migration.testing:bar',
            ],
          },
          'migration.testing:bar-tests': {
            'builders': [
              'migration.testing:bar',
              'migration.testing:bar-tests',
              'tryserver.migration.testing:bar',
            ],
          },
          'tryserver.migration.testing:bar': {
            'builders': [
              'migration.testing:bar',
              'migration.testing:bar-tests',
              'tryserver.migration.testing:bar',
            ],
          },
        },
      )

  def test_validate_groupings(self):
    with tempfile.TemporaryDirectory() as d:
      with self.assertRaises(subprocess.CalledProcessError) as caught:
        _execute_generate_groupings(
          '--groupings-dir', d, 'migration-testing', '--validate'
        )

      self.assertIn(
        'The following groupings files need regeneration: migration-testing',
        caught.exception.output,
      )


if __name__ == '__main__':
  unittest.main()
