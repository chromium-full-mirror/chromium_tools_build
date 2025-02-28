#!/usr/bin/env vpython3
# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import argparse
import json
import logging
import base64
import sys
import subprocess
import time

SUB_NAME = 'projects/chrome-pernosco/subscriptions/pernosco-link-sub'
MAX_RETRY = 6


def parse_args(args):
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument(
      '--output-json', required=True, help='path to output json for results')
  parser.add_argument(
      '--request_ids',
      required=True,
      action='append',
      help='The list of request id to be fetch.')
  return parser.parse_args(args)


def pull_pubsub_info():
  check_pubsub = (f'gcloud pubsub subscriptions pull {SUB_NAME} '
                  f'--format=json --limit=1000')
  json_result = []
  with subprocess.Popen([*check_pubsub.split()],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE) as process:
    output, _ = process.communicate()
    json_result = json.loads(output) if output else []
    for json_message in json_result:
      # Decode the data field of the pubsub results.
      decode_byte = base64.b64decode(json_message['message']['data'])
      json_message['message']['data'] = json.loads(decode_byte.decode('utf-8'))
  return json_result


def main(args):
  args = parse_args(args)
  logging.getLogger().setLevel(logging.INFO)

  target_requests = args.request_ids
  output = []
  ack_ids = []

  for attempt in range(0, MAX_RETRY):
    results = pull_pubsub_info()
    i = 0
    while i < len(target_requests):
      found = False
      for result in results:
        if target_requests[i] == result['message']['data']['requestId']:
          output.append({
              'request_id': target_requests[i],
              'ack_id': result['ackId'],
              'trace_id': result['message']['data']['traceId'],
          })
          ack_ids.append(result['ackId'])
          target_requests.pop(i)
          found = True
          break
      if not found:
        i += 1
    if len(target_requests) > 0:
      time.sleep(200)
      print(f'Attempt {attempt + 1} times failed, sleep 200 seconds')
    else:
      break

  ack_pubsub = f'gcloud pubsub subscriptions ack {SUB_NAME}'
  with subprocess.Popen(
      [*ack_pubsub.split() + [f'--ack-ids={",".join(ack_ids)}']],
      stdout=subprocess.PIPE,
      stderr=subprocess.PIPE) as process:
    process.communicate()
  with open(args.output_json, 'w+', encoding='utf-8') as f:
    json.dump(output, f)
  sys.exit(0)


if __name__ == '__main__':
  main(sys.argv[1:])
