# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import argparse
import json
import requests
from requests import utils
import sys

_CABE_URL = 'https://cabe-http.luci.app'
_GET_ANALYSIS_ENDPOINT = 'getanalysis'


def _generate_cabe_request_url(args):
  return f'{_CABE_URL}/{_GET_ANALYSIS_ENDPOINT}/{args.job_id}'


def _get_cabe_analysis(args):
  headers = utils.default_headers()

  if args.token:
    with open(args.token, encoding='utf-8') as oauth_token_fd:
      headers['Authorization'] = 'Bearer %s' % oauth_token_fd.read()

  url = _generate_cabe_request_url(args)
  response = requests.get(url, headers=headers)

  return response


def parse_args():
  parser = argparse.ArgumentParser(
    description='Parameters to poll Pinpoint try job analysis results.'
  )

  parser.add_argument(
    '--job_id',
    type=str,
    required=True,
    help='Id of the try job. If empty.',
  )
  parser.add_argument(
    '--token',
    help='Raw IO with oauth token string used for send request.',
  )

  return parser.parse_args()


def main():
  args = parse_args()

  # cabe response:
  # {
  #   'Benchmark': 'speedometer3',
  #   'Results': {
  #      'Charts-chartjs': {stat data}
  #    }
  # }
  data = {}
  cabe_resp = _get_cabe_analysis(args)
  cabe_json = cabe_resp.json()
  if not cabe_json:
    # cabe fails return anything.
    return 1
  benchmark = cabe_json.get('Benchmark')
  if benchmark is None:
    # no regression is found.
    return 0
  data['benchmark'] = benchmark
  data['regressions'] = cabe_json.get('Results', {})
  json.dump(data, sys.stdout)
  return 0


if __name__ == '__main__':
  sys.exit(main())
