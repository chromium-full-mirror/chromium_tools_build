#!/usr/bin/env vpython3
# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import argparse
import json
import logging
import sys

from google.cloud import bigquery

PROJECT = 'chrome-flakiness'
FETCH_BUILDER_QUERY = """
    SELECT DISTINCT
      builder_name,
      builder_project,
      bucket,
    FROM
      `chrome-flakiness.flake_endorser.test_history_submitted_tests`
  """

# This query utilizes this data to determine the test history per cq try builder.
TEST_HISTORY_QUERY = """
    SELECT DISTINCT
      test_id,
      variant_hash
    FROM
      `chrome-flakiness.flake_endorser.test_history_submitted_tests`
    WHERE
      builder_name = \'{}\' AND
      builder_project = \'{}\' AND
      bucket = \'{}\'
"""


def fetch_builders(bq, args):
  logging.info('Searching for all try builders.')
  logging.info(FETCH_BUILDER_QUERY)
  query_job = bq.query(FETCH_BUILDER_QUERY)
  rows = query_job.result()
  logging.info('Query complete. Processing results.')
  builders = [
    {
      'builder_name': row.builder_name,
      'builder_project': row.builder_project,
      'bucket': row.bucket,
    }
    for row in rows
  ]

  with open(args.output_file, 'w') as f:
    json.dump(builders, f)

  return builders


def query_test_history(bq, args):
  builder = args.builder
  project = args.project
  builder_bucket = args.builder_bucket
  logging.info(
    'Searching test history for %s:%s:%s' % (project, builder_bucket, builder)
  )
  query = TEST_HISTORY_QUERY.format(builder, project, builder_bucket)
  logging.info(query)
  query_job = bq.query(query)
  query_job.result()
  logging.info('Query completed. Uploading results to GS bucket.')

  gs_bucket_path = args.export_gs_path
  logging.info('GS bucket: %s' % gs_bucket_path)

  dataset_id = str(query_job.destination.dataset_id)
  table_id = query_job.destination.table_id
  dataset_ref = bigquery.DatasetReference(PROJECT, dataset_id)
  table_ref = dataset_ref.table(table_id)

  logging.info('Extracting temp table to GS.')
  job_config = bigquery.job.ExtractJobConfig()
  job_config.destination_format = (
    bigquery.job.DestinationFormat.NEWLINE_DELIMITED_JSON
  )

  extract_job = bq.extract_table(
    table_ref, gs_bucket_path, job_config=job_config
  )
  extract_job.result()


def format_file(bq, args):
  results = []
  logging.info('Formatting results into json object.')
  try:
    for json_file in args.file:
      with open(json_file, 'r') as f:
        for line in f:
          results.append(json.loads(line))
  except Exception as e:
    logging.error('Failed formatting file %s. line: %s' % (json_file, line))
    logging.error(e)
    raise e

  logging.info('Writing out to disk...')
  try:
    with open(args.output_file, 'w') as f:
      json.dump(results, f)
  except Exception as e:
    logging.error('Failed to write file to disk.')
    logging.error(e)
    raise e

  return json.dumps(results)


def parse_arguments(args):
  parser = argparse.ArgumentParser()
  subparsers = parser.add_subparsers()

  builder_parser = subparsers.add_parser(
    'builders', help='get list of all supported try builders'
  )
  builder_parser.set_defaults(func=fetch_builders)
  builder_parser.add_argument(
    '--output-file', required=True, help='path to output file for results'
  )

  test_history_parser = subparsers.add_parser(
    'history', help='fetch test history'
  )
  test_history_parser.set_defaults(func=query_test_history)
  test_history_parser.add_argument(
    '--export-gs-path',
    required=True,
    help=('GS bucket path to export the table data to.'),
  )
  test_history_parser.add_argument(
    '--builder', required=True, help='try builder name'
  )
  test_history_parser.add_argument(
    '--builder-bucket', required=True, help='try builder bucket in project'
  )
  test_history_parser.add_argument(
    '--project', required=True, help='try builder project'
  )

  format_parser = subparsers.add_parser(
    'format', help='format new line delimited json to json format'
  )
  format_parser.set_defaults(func=format_file)
  format_parser.add_argument(
    '--file', required=True, action='append', help='path(s) to file'
  )
  format_parser.add_argument(
    '--output-file', required=True, help='path to output file for result'
  )

  return parser.parse_args(args)


def main(args):
  bq = bigquery.client.Client(project=PROJECT)
  args = parse_arguments(args)
  args.func(bq, args)
  sys.exit(0)


if __name__ == '__main__':
  logging.basicConfig(
    format='[%(asctime)s %(levelname)s] %(message)s', level=logging.INFO
  )
  main(sys.argv[1:])
