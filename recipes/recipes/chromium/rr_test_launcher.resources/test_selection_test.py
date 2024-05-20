#!/usr/bin/env vpython3
# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import mock
import os
import unittest

from google.cloud import bigquery
import test_selection


class QueryTest(unittest.TestCase):

  def test_args(self):
    output_file = '/some/path/to/file.json'
    query_args = [
        '--output-file',
        output_file,
        '--sample-day=10',
    ]

    res = test_selection.parse_args(query_args)
    self.assertEqual(res.output_file, output_file)
    self.assertEqual(res.sample_day, 10)

  @mock.patch('google.cloud.bigquery.Client', autospec=True)
  @mock.patch('builtins.open', autospec=True)
  def test_fetch_builders(self, mock_file, mock_client):
    output_file = 'test_result.json'
    builder_args = [
        '--output-file',
        output_file,
        '--sample-day=10',
    ]
    args = test_selection.parse_args(builder_args)
    test_selection.fetch_test_info(mock_client, args)

    query_file = os.path.join(os.path.dirname(__file__), 'test_selection.sql')
    with open(query_file, 'r', encoding='utf-8') as f:
      query_str = f.read()
    query_result = query_str.format(sample_day=args.sample_day)

    mock_client.query.assert_called_with(query_result)
    mock_file.assert_called_with(query_file, 'r', encoding='utf-8')


if __name__ == '__main__':
  unittest.main()
