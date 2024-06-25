#!/usr/bin/env vpython3
# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import argparse
import json
import logging
import os
import sys

from google.cloud import bigquery

PROJECT = 'chrome-unexpected-pass-data'


def parse_args(args):
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument(
      '--output-json', required=True, help='path to output json for results')
  parser.add_argument(
      '--sample-day',
      required=True,
      type=int,
      choices=range(1, 30),
      help='Sample days from now to select bugs in range of 1 to 30')
  return parser.parse_args(args)


def fetch_test_info(bq, args):
  query_file = os.path.join(os.path.dirname(__file__), 'test_selection.sql')
  with open(query_file, 'r', encoding='utf-8') as f:
    query_str = f.read()
  query = query_str.format(sample_day=args.sample_day)

  logging.info('Searching for all failed tests.')
  query_job = bq.query(query)
  rows = query_job.result()
  logging.info('Query complete. Processing results.')
  test_results = [{
      'test_id': row.test_id,
      'bug_id': row.bug_id,
      'invocation_id': row.invocation_id,
      'test_suite': row.test_suite,
      'builder': row.builder,
  } for row in rows]

  with open(args.output_json, 'w+', encoding='utf-8') as f:
    json.dump(test_results, f)


def main(args):
  args = parse_args(args)
  logging.getLogger().setLevel(logging.INFO)

  bq = bigquery.client.Client(project=PROJECT)
  fetch_test_info(bq, args)
  sys.exit(0)


if __name__ == '__main__':
  main(sys.argv[1:])
