#!/usr/bin/env vpython3
# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import os
import sys
import unittest

import mock

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(
  0, os.path.abspath(os.path.join(THIS_DIR, os.pardir, 'resources'))
)

import blame_util


class BlameUtilTest(unittest.TestCase):
  # This tests the case where blamelist for a file README.md looks like
  #                     ________________
  # 1 john@chromium.org| Content Line 1 |
  # 2 jane@chromium.org| Content Line 2 |
  # 3                  | Content Line 3 |
  #
  # where line 1 was modified before 4 weeks
  @mock.patch('blame_util.subprocess.check_output', autospec=True)
  def test_generate_blame_list(self, mock_subprocess):
    blame_lines = [
      '^9041ee4b83 (<john@chromium.org >2022-10-05 15:19:11 +0000   1) Content line 1',
      '47faf11ca3d (<jane@chromium.org> 2022-10-05 15:19:11 +0000   2) Content line 2',
      '47faf11ca3d (<jane@chromium.org> 2022-10-05 15:19:11 +0000   3) Content line3',
    ]

    mock_subprocess.side_effect = ['\n'.join(blame_lines)]
    response = blame_util.generate_blame_list(
      'path/to/src', ['//README.md'], num_weeks=4
    )
    self.assertDictEqual(
      {'//README.md': {'jane@chromium.org': [2, 3]}}, response
    )


if __name__ == '__main__':
  unittest.main()
