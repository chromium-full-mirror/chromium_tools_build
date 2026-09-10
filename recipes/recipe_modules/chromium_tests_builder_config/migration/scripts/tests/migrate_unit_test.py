#!/usr/bin/env vpython3
# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import os
import sys
import unittest

SCRIPTS_DIR = os.path.normpath(os.path.join(__file__, '..', '..'))

sys.path.append(SCRIPTS_DIR)

import migrate


class MigrateUnitTest(unittest.TestCase):
  def test_parse_args(self):
    args = migrate.parse_args(['no-colon', 'too:many:colons', 'just:right'])
    self.assertEqual(
      args.builders, ['no-colon', 'too:many:colons', 'just:right']
    )

  def test_invalid_get_builders_to_migrate(self):
    args = migrate.parse_args(['no-colon', 'too:many:colons', 'just:right'])
    with self.assertRaises(migrate.InvalidBuilderError) as caught:
      migrate.get_builders_to_migrate(args)
    self.assertCountEqual(
      caught.exception.invalid_builders, ['no-colon', 'too:many:colons']
    )

  def test_get_builders_to_migrate(self):
    args = migrate.parse_args(
      ['group1:builder1', 'group2:builder2', 'group3:builder3']
    )
    builders_to_migrate = migrate.get_builders_to_migrate(args)
    self.assertCountEqual(
      builders_to_migrate,
      [('group1', 'builder1'), ('group2', 'builder2'), ('group3', 'builder3')],
    )


if __name__ == '__main__':
  unittest.main()
