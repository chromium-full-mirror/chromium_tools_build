# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import argparse
import json
import requests
from requests import utils
import sys
import urllib.parse

STAGING_PINPOINT_URL = 'https://pinpoint-dot-chromeperf-stage.uc.r.appspot.com'
PINPOINT_URL = 'https://pinpoint-dot-chromeperf.appspot.com'
NEW_JOB_ENDPOINT = '/api/new'


def invoke_pinpoint_try_job(url, token):
  headers = utils.default_headers()
  if token:
    with open(token, encoding='utf-8') as oauth_token_fd:
      headers['Authorization'] = 'Bearer %s' % oauth_token_fd.read()

  response = requests.post(url, headers=headers)

  return response


def generate_request_url(params, use_staging=False):
  pinpoint_url = STAGING_PINPOINT_URL if use_staging else PINPOINT_URL
  url = f'{pinpoint_url}{NEW_JOB_ENDPOINT}?{urllib.parse.urlencode(params)}'

  return url


def parse_args():
  parser = argparse.ArgumentParser(
    description='Parameters to triger Pinpoint pairwise try job.'
  )

  parser.add_argument(
    '--name',
    type=str,
    help='Name of the try job. If empty, default name will apply: '
    'CQ Perf on {benchmark}/{story}',
  )
  parser.add_argument(
    '--base_git_hash',
    type=str,
    required=True,
    help='Base Chromium/src Git hash for comparison.',
  )
  parser.add_argument(
    '--patch',
    type=str,
    required=True,
    help='Patch (Gerrit URL) for the try job. E.g.: '
    'https://chromium-review.googlesource.com/c/chromium/src/+/12345/6',
  )
  parser.add_argument(
    '--configuration',
    type=str,
    required=True,
    help='Bot configuration for the try job. E.g., win-10-perf',
  )
  parser.add_argument(
    '--benchmark',
    type=str,
    required=True,
    help='Benchmark to run in the try job.',
  )
  parser.add_argument(
    '--story',
    type=str,
    required=True,
    help='Story to run within the benchmark.',
  )
  parser.add_argument(
    '--attempts_count',
    type=int,
    help='Number of iterations on test runs from each branch.',
  )
  parser.add_argument(
    '--user',
    type=str,
    help='User initiating the try job. The default is "Perf On CQ"',
  )
  parser.add_argument('--tags', help='JSON string of tags for the try job. ')
  parser.add_argument(
    '--token',
    help='Raw IO with oauth token string used for send request.',
  )
  parser.add_argument(
    '--use_staging',
    action='store_true',
    help='Use staging environment if True.',
  )

  return parser.parse_args()


def generate_params_from_args(args):
  params = {}
  params['comparison_mode'] = 'try'
  params['name'] = args.name or f'CQ Perf on {args.benchmark}/{args.story}'
  params['base_git_hash'] = args.base_git_hash
  params['end_git_hash'] = args.base_git_hash
  params['base_patch'] = ''
  params['experiment_patch'] = args.patch
  params['configuration'] = args.configuration
  params['benchmark'] = args.benchmark
  params['story'] = args.story
  params['initial_attempt_count'] = args.attempts_count
  params['repository'] = 'chromium'
  params['user'] = args.user or 'Perf On CQ'
  params['tags'] = args.tags

  return params


def main():
  args = parse_args()
  params = generate_params_from_args(args)
  use_staging = args.use_staging or False
  token = args.token
  request_url = generate_request_url(params=params, use_staging=use_staging)
  resp = invoke_pinpoint_try_job(url=request_url, token=token)
  data = {
    'request_url': request_url,
    'params': params,
    'response': resp.json(),
  }
  print(json.dumps(data))
  if resp.status_code == 200:
    return 0
  return 1


if __name__ == '__main__':
  sys.exit(main())
