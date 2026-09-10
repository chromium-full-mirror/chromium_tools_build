#!/usr/bin/env vpython3
# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import unittest

_SCRIPT = os.path.normpath(f'{__file__}/../../migrate.py')


def _execute_migrate(*args):
  cmd = [sys.executable, _SCRIPT] + list(args)
  return subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)


class MigrateIntegrationTest(unittest.TestCase):
  def test_invalid_arguments(self):
    with self.assertRaises(subprocess.CalledProcessError) as caught:
      _execute_migrate('no-colon', 'too:many:colons', 'just:right')
    self.assertIn(
      (
        "The following builders to migrate are invalid: "
        "'no-colon', 'too:many:colons'"
      ),
      caught.exception.output,
    )

  def test_migration_error(self):
    with self.assertRaises(subprocess.CalledProcessError) as caught:
      _execute_migrate('unknown-group:builder')
    self.assertIn(
      "unknown builder 'unknown-group:builder'", caught.exception.output
    )

  def test_migration(self):
    try:
      output = _execute_migrate('migration.testing:bar')
    except subprocess.CalledProcessError as e:
      sys.stderr.write(e.output)
      raise
    self.maxDiff = None
    self.assertEqual(
      output,
      textwrap.dedent('''\
            migration.testing:bar
                builder_spec = builder_config.builder_spec(
                    gclient_config = builder_config.gclient_config(
                        config = "chromium",
                    ),
                    chromium_config = builder_config.chromium_config(
                        config = "chromium",
                    ),
                ),

            migration.testing:bar-tests
                builder_spec = builder_config.builder_spec(
                    execution_mode = builder_config.execution_mode.TEST,
                    gclient_config = builder_config.gclient_config(
                        config = "chromium",
                    ),
                    chromium_config = builder_config.chromium_config(
                        config = "chromium",
                    ),
                ),

            tryserver.migration.testing:bar
                mirrors = [
                    "ci/bar",
                ],

            '''),
    )


if __name__ == '__main__':
  unittest.main()
