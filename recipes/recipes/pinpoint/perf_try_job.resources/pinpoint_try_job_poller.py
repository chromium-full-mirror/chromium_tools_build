# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
'''Script to poll the status of a pinpoint job'''

import argparse
import json
import requests
from requests import utils
import sys
import time

_CABE_URL = 'https://cabe.skia.org'
_GET_ANALYSIS_ENDPOINT = 'getanalysis'
_POLL_INTERVAL = 30  # 30 seconds
_POLL_TIMEOUT = 7200  # 2 hours
_PINPOINT_URL = 'https://pinpoint-dot-chromeperf.appspot.com'
_STAGING_PINPOINT_URL = 'https://pinpoint-dot-chromeperf-stage.uc.r.appspot.com'
_GET_JOB_ENDPOINT = '/api/job/'
_MAX_RETRY_ON_ERROR = 5


def _generate_cabe_request_url(args):
  return f'{_CABE_URL}/{_GET_ANALYSIS_ENDPOINT}/{args.job_id}'


def _generate_legacy_pinpoint_request_url(args):
  pinpoint_url = _STAGING_PINPOINT_URL if args.use_staging else _PINPOINT_URL
  return f'{pinpoint_url}/{_GET_JOB_ENDPOINT}/{args.job_id}'


def _poll_pinpoint_job(args):
  headers = utils.default_headers()
  if args.token:
    with open(args.token, encoding='utf-8') as oauth_token_fd:
      headers['Authorization'] = 'Bearer %s' % oauth_token_fd.read()

  url = _generate_legacy_pinpoint_request_url(args)
  response = requests.get(url, headers=headers)

  return response


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
    help='Id of the try job.',
  )
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


def main():
  args = parse_args()

  start = time.time()
  job_status = 'Unknown'
  retry_counter = 0  # retry for transient errors.
  error_msg = ''
  while True:
    current = time.time()
    elapsed = current - start
    if elapsed > _POLL_TIMEOUT:
      error_msg = 'Poller script timed out.'
      break

    resp = _poll_pinpoint_job(args)
    resp_json = resp.json()
    job_status = resp_json.get('status')
    if job_status in ['Completed', 'Failed', 'Cancelled']:
      # job finished. Exit loop.
      break
    if resp_json.get('error'):
      retry_counter += 1
      # give up after trying MAX times
      if retry_counter > _MAX_RETRY_ON_ERROR:
        error_msg = 'Poller failed to get job status after max try counts.'
        break
    # re-poll after INTERVAL
    time.sleep(_POLL_INTERVAL)

  data = {
    'job_id': args.job_id,
    'status': job_status,
    'error': error_msg,
  }
  print(json.dumps(data))
  # If Pinpoint does not finish as expected (timeout or error out), return 1.
  if error_msg != '':
    return 1
  return 0


if __name__ == '__main__':
  sys.exit(main())
