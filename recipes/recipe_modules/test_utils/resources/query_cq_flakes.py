#!/usr/bin/env python3
# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

# Endpoint to get flaky tests. The request and response formats are defined as
# _CQFlakesRequest and _CQFlakeResponse in:
# https://chromium.googlesource.com/infra/infra/+/7a355f3cbfd08acdb2579e0506924516330e8700/appengine/findit/endpoint_api.py#193
_FLAKE_SERVICE_ENDPOINT = (
  'https://findit-for-me.appspot.com/_ah/api/findit/v1/get_cq_flakes'
)


def query_and_write_flakes(input_path, output_path):
  """Queries and writes the flakes to a file.

  Args:
    input_path (str): Absolute path to a file whose content conforms to
                      _CQFlakesRequest.
    output_path (str): Absolute path to a file to write flakes to, and the
                       format conforms to _CQFlakeResponse.
  """
  with open(input_path, 'r') as f:
    input_json = json.load(f)

  response = urllib.request.urlopen(
    urllib.request.Request(
      url=_FLAKE_SERVICE_ENDPOINT, data=json.dumps(input_json).encode()
    )
  )
  with open(output_path, 'wb') as f:
    f.write(response.read())


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument(
    '--input-path',
    required=True,
    type=str,
    help='Absolute path to a file that defines to list of tests to query',
  )
  parser.add_argument(
    '--output-path',
    required=True,
    type=str,
    help='Absolute path to a file that stores the queries flaky tests on cq',
  )

  args = parser.parse_args()
  if not os.path.isfile(args.input_path):
    parser.error('%s is not an existing file' % args.input_path)

  query_and_write_flakes(args.input_path, args.output_path)


if __name__ == '__main__':
  sys.exit(main())
