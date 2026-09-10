#!/usr/bin/env vpython3
# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import os
import sys
import unittest

from unittest import mock

from pyfakefs.fake_filesystem_unittest import TestCase

SCRIPTS_DIR = os.path.normpath(f'{__file__}/../..')

sys.path.append(SCRIPTS_DIR)

import buildozer_wrapper


class BuildozerWrapperUnitTest(TestCase):
  def setUp(self):
    self.setUpPyfakefs()

  @mock.patch('buildozer_wrapper._execute_buildozer', autospec=True)
  def test_perform_edits_ci(self, mock_execute_buildozer):
    buildozer_wrapper.perform_edits(
      buildozer_binary='fake-buildozer',
      infra_config_dir='/infra/config',
      output_dir='/output',
      builder_group='fake-builder-group',
      edits_by_builder={
        'fake-builder1': {
          'foo': '"new foo value"',
        },
        'fake-builder2': {
          'bar': 'new-bar-value',
          'baz': 'new-baz-value',
        },
      },
    )

    self.assertEqual(
      mock_execute_buildozer.call_args_list,
      [
        mock.call(
          buildozer_binary='fake-buildozer',
          input_file_path=(
            '/infra/config/subprojects/chromium/ci/fake-builder-group.star'
          ),
          output_file_path=(
            '/output/subprojects/chromium/ci/fake-builder-group.star'
          ),
          commands=[
            'set foo "new\\ foo\\ value"|-:fake-builder1',
            'set bar new-bar-value|set baz new-baz-value|-:fake-builder2',
          ],
        )
      ],
    )

  @mock.patch('buildozer_wrapper._execute_buildozer', autospec=True)
  def test_perform_edits_try(self, mock_execute_buildozer):
    buildozer_wrapper.perform_edits(
      buildozer_binary='fake-buildozer',
      infra_config_dir='/infra/config',
      output_dir='/output',
      builder_group='tryserver.fake-builder-group',
      edits_by_builder={
        'fake-builder1': {
          'foo': '"new foo value"',
        },
        'fake-builder2': {
          'bar': 'new-bar-value',
          'baz': 'new-baz-value',
        },
      },
    )

    self.assertEqual(
      mock_execute_buildozer.call_args_list,
      [
        mock.call(
          buildozer_binary='fake-buildozer',
          input_file_path=(
            '/infra/config/subprojects/chromium/try/'
            'tryserver.fake-builder-group.star'
          ),
          output_file_path=(
            '/output/subprojects/chromium/try/tryserver.fake-builder-group.star'
          ),
          commands=[
            'set foo "new\\ foo\\ value"|-:fake-builder1',
            'set bar new-bar-value|set baz new-baz-value|-:fake-builder2',
          ],
        )
      ],
    )


if __name__ == '__main__':
  unittest.main()
