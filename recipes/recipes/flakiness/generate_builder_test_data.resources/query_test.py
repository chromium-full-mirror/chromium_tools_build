#!/usr/bin/env vpython3
# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import json
import mock
import os
import unittest
import subprocess
import sys

from google.cloud import bigquery

THIS_DIR = os.path.dirname(__file__)

sys.path.insert(
    0,
    os.path.abspath(
        os.path.join(THIS_DIR, '..', 'generate_builder_test_data.resources')))

import query


class QueryTest(unittest.TestCase):

  def test_args(self):
    output_file = '/some/path/to/file.json'
    builder_args = [
        'builders',
        '--output-file',
        output_file,
    ]

    res = query.parse_arguments(builder_args)
    self.assertEqual(res.output_file, output_file)

    output_gs_path = 'gs://some/path/test.json'
    builder = 'fake_builder'
    project = 'chromium'
    builder_bucket = 'try'
    history_args = [
        'history',
        '--export-gs-path',
        output_gs_path,
        '--builder',
        builder,
        '--project',
        project,
        '--builder-bucket',
        builder_bucket,
    ]
    res = query.parse_arguments(history_args)
    self.assertEqual(res.export_gs_path, output_gs_path)
    self.assertEqual(res.builder, builder)

    file = '/some/file.json'
    format_args = ['format', '--file', file, '--output-file', output_file]
    res = query.parse_arguments(format_args)
    self.assertEqual(res.file, [file])
    self.assertEqual(res.output_file, output_file)

  @mock.patch(
      'builtins.open',
      new_callable=mock.mock_open,
      read_data='{\"foo\":\"bar\"}\n{\"hello\":\"world\"}')
  def test_format_file(self, mock_file):
    file = '/some/file.json'
    output_file = '/some/path/to/file.json'
    format_args = ['format', '--file', file, '--output-file', output_file]
    args = query.parse_arguments(format_args)
    res_json = query.format_file(None, args)
    mock_file.assert_called_with(output_file, 'w')
    result = [{"foo": "bar"}, {"hello": "world"}]
    self.assertEqual(json.dumps(result), res_json)

  @mock.patch('google.cloud.bigquery.Client', autospec=True)
  @mock.patch('builtins.open', autospec=True)
  def test_fetch_builders(self, mock_file, mock_client):
    output_file = '/some/path/to/file.json'
    builder_args = [
        'builders',
        '--output-file',
        output_file,
    ]
    args = query.parse_arguments(builder_args)
    query.fetch_builders(mock_client, args)

    mock_client.query.assert_called_with(query.FETCH_BUILDER_QUERY)
    mock_file.assert_called_with(output_file, 'w')

  @mock.patch('google.cloud.bigquery.Client', autospec=True)
  def test_query_test_history(self, mock_client):
    output_gs_path = 'gs://some/path/test.json'
    builder = 'fake_builder'
    project = 'chromium'
    builder_bucket = 'try'
    history_args = [
        'history',
        '--export-gs-path',
        output_gs_path,
        '--builder',
        builder,
        '--project',
        project,
        '--builder-bucket',
        builder_bucket,
    ]
    args = query.parse_arguments(history_args)
    query.query_test_history(mock_client, args)

    job_config = bigquery.job.ExtractJobConfig()
    job_config.destination_format = (
        bigquery.job.DestinationFormat.NEWLINE_DELIMITED_JSON)

    mock_client.query.assert_called_with(
        query.TEST_HISTORY_QUERY.format(builder, project, builder_bucket))
    mock_client.extract_table.assert_called_with(
        mock.ANY, output_gs_path, job_config=mock.ANY)


if __name__ == '__main__':
  unittest.main()
