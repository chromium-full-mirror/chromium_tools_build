# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import argparse
import os
import pprint
import requests
import sys

API_URL = 'https://decisiongraph-pa.googleapis.com/v1/rundecisiongraph'
DECISION_GRAPH_NAME = 'sts_chrome_fetch_results'
STAGE_ID = 'fetch_tests_to_skip_for_%d_%d_%d'
STAGE_NAME = 'smart_test_selection_stage'
PROJECT = 'chromium/src'
BRANCH = 'main'
HOSTNAME = 'chromium'  # Only the first word of gerrit host i.e. %s-review.googlesoure.com
LOCATION_ENUM = 1
STAGE_SERVICE_GSLB = 'blade:test-relevance-stage-service-prod-luci'
# TODO(b/405145095): Change this to a lower value after decisiongraph has moved
# to spanner queues.
MAX_DURATION_SECONDS = 900
TIMEOUT_SECONDS = MAX_DURATION_SECONDS + 60
MAX_ATTEMPTS = 3
BLOCKING_ENUM = 2

BATCH_SIZE = 5

def read_api_key(file_path):
  """
    Reads the API key from a file.

    :param file_path: Path to the file containing the API key.
    :return: The API key as a string.
    """
  try:
    with open(file_path, "r", encoding="utf-8") as file:
      return file.read().strip()
  except FileNotFoundError:
    print(f"Error: API key file not found at {file_path}")
    sys.exit(1)
  except Exception as e:
    print(f"An error occurred while reading the API key: {e}")
    sys.exit(1)


def fetch_api_data(url, json=None):
  """
    Sends an HTTP GET request to the specified API and returns the JSON response.

    :param url: The API endpoint URL.
    :param json: The payload to send with request.
    :return: The JSON response as a dictionary, or None if the request fails.
    """
  try:
    response = requests.post(url, json=json, timeout=TIMEOUT_SECONDS)
    print(response.text)
    print(response.status_code)
    print(response.json)
    response.raise_for_status(
    )  # Raise an HTTPError for bad responses (4xx and 5xx)
    return response.json()
  except requests.exceptions.RequestException as e:
    print(f"An error occurred: {e}")
    return None


def overwrite_filter_file(filter_file_dir, test_suite, tests_to_skip):
  """
  Overwrites a filter file with tests to skip.

  :param filter_file_dir: The directory containing the filter files.
  :param test_suite: The name of the test suite (e.g., 'browser_tests').
  :param tests_to_skip: A list of test names to be written to the file.
  """
  file_name = f"{test_suite}.filter"
  file_path = os.path.join(filter_file_dir, file_name)

  if not os.path.exists(file_path):
    print(f"Error: Filter file not found at {file_path}")
    return False

  try:
    with open(file_path, "w", encoding="utf-8") as f:
      for test_name in tests_to_skip:
        f.write(f"-{test_name}*\n")
    print(f"Successfully wrote {len(tests_to_skip)} tests to {file_path}")
    return True
  except Exception as e:
    print(f"An error occurred while writing to {file_path}: {e}")
    return False


def main():
  parser = argparse.ArgumentParser(
      description="Fetch data from an API with query parameters.")
  parser.add_argument(
      "--test-targets",
      required=True,
      nargs='+',
      type=str,
      help="Name of the test targets e.g. browser_tests.")
  parser.add_argument(
      "--build-id",
      required=True,
      type=str,
      help="Build id of the current build.")
  parser.add_argument(
      "--change", required=True, type=int, help="Current change number.")
  parser.add_argument(
      "--patchset", required=True, type=int, help="Current patchset number.")
  parser.add_argument(
      "--builder",
      required=True,
      type=str,
      help="Name of the builder e.g. linux-rel.")
  parser.add_argument(
      "--api-key-file",
      required=True,
      type=str,
      help="Path to file containing API key for Android Decisiongraph API")
  parser.add_argument(
      "--filter-file-dir",
      required=True,
      type=str,
      help="Path to dir containing .filter files which will be overwritten")
  args = parser.parse_args()

  # Find corresponding "main" CQ builder name
  canonical_builder = args.builder
  if args.builder.endswith('-test-selection'):
    canonical_builder = args.builder[:args.builder.rfind("-test-selection")]

  test_target_batches = [
      args.test_targets[i:i + BATCH_SIZE]
      for i in range(0, len(args.test_targets), BATCH_SIZE)
  ]

  return_status = 0
  for batch_idx, test_target_batch in enumerate(test_target_batches):
    checks = []
    print("batch num = %d" % batch_idx)
    for test_target in test_target_batch:
      check = {
          'identifier': {
              'luci_test': {
                  'project': PROJECT,
                  'branch': BRANCH,
                  'builder': canonical_builder,
                  'test_suite': test_target,
              }
          },
          'run': {
              'luci_test': {
                  'build_id': args.build_id
              }
          },
      }
      checks.append(check)

    # See http://google3/google/internal/android/treehugger/decisiongraph/proto/decision_graph.proto
    # to know request proto structure
    payload = {
        'graph': {
            'name':
                DECISION_GRAPH_NAME,
            'stages': [{
                'stage': {
                    'id': STAGE_ID % (args.change, args.patchset, batch_idx),
                    'name': STAGE_NAME,
                },
                'execution_options': {
                    'location': LOCATION_ENUM,
                    'address': STAGE_SERVICE_GSLB,
                    # prepare = False triggers fetch stored results path in decisiongraph api
                    'prepare': False,
                    'max_duration': {
                        'seconds': MAX_DURATION_SECONDS
                    },
                    'max_attempts': MAX_ATTEMPTS,
                    'blocking': BLOCKING_ENUM,
                },
            }],
        },
        'input': [{
            'stage': {
                'id': STAGE_ID % (args.change, args.patchset, batch_idx),
                'name': STAGE_NAME,
            },
            'input': [{
                'checks': checks,
            }],
            'changes': {
                'changes': [{
                    'hostname': HOSTNAME,
                    'change_number': args.change,
                    'patchset': args.patchset,
                }],
            },
        }]
    }
    print("payload = ")
    pprint.pprint(payload)
    # Read the API key from the specified file
    api_key = read_api_key(args.api_key_file)

    request_with_key = '%s?key=%s' % (API_URL, api_key)
    response_data = fetch_api_data(url=request_with_key, json=payload)

    if response_data:
      stage_outputs = response_data['outputs']
      assert len(stage_outputs) == 1
      checks = stage_outputs[0]['checks']
      for check in checks:
        children = check['children']
        test_suite = check['identifier']['luci_test']['test_suite']
        tests_to_skip = []
        for child in children:
          tests_to_skip.append(child['identifier']['luci_test']['test_id'])
        if not overwrite_filter_file(args.filter_file_dir, test_suite,
                                     tests_to_skip):
          return_status = 1
    else:
      print("Failed to fetch data from the API.")
      return_status = 1

  sys.exit(return_status)


if __name__ == "__main__":
  main()
