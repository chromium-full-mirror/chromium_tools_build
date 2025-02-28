#!/usr/bin/env python3
# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import unittest
from unittest.mock import patch, mock_open

import pernosco_result_fetch


class PernoscoResultFetchTest(unittest.TestCase):

  def test_args(self):
    output_json = '/some/path/to/file.json'
    args = [
        '--output-json',
        output_json,
        '--request_ids=id1',
        '--request_ids=id2',
    ]
    expected = ['id1', 'id2']
    res = pernosco_result_fetch.parse_args(args)
    self.assertEqual(res.request_ids, expected)


if __name__ == '__main__':
  unittest.main()
