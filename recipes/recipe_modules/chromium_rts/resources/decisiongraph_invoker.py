# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import argparse
import requests
import sys

TIMEOUT_SECONDS = 300
API_URL = 'https://decisiongraph-pa.googleapis.com/v1/rundecisiongraph'
DECISION_GRAPH_NAME = 'smart_test_selection_graph_chrome'
STAGE_ID = 'test_selection_for_%s_%d_%d'
STAGE_NAME = 'smart_test_selection_stage'
PROJECT = 'chromium/src'
BRANCH = 'main'
HOSTNAME = 'chromium'  # Only the first word of gerrit host i.e. %s-review.googlesoure.com
LOCATION_ENUM = 1
STAGE_SERVICE_GSLB = 'blade:test-relevance-stage-service-prod-luci'
MAX_DURATION_SECONDS = 180
MAX_ATTEMPTS = 3
BLOCKING_ENUM = 2


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
    response.raise_for_status(
    )  # Raise an HTTPError for bad responses (4xx and 5xx)
    return response.json()
  except requests.exceptions.RequestException as e:
    print(f"An error occurred: {e}")
    return None


if __name__ == "__main__":
  parser = argparse.ArgumentParser(
      description="Fetch data from an API with query parameters.")
  parser.add_argument(
      "--test-target",
      required=True,
      type=str,
      help="Name of the test target e.g. browser_tests.")
  parser.add_argument(
      "--build_id",
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
      "--api_key_file",
      required=True,
      type=str,
      help="Path to file containing API key for Android Decisiongraph API")
  args = parser.parse_args()

  # Find corresponding "main" CQ builder name
  canonical_builder = args.builder
  if args.builder.endswith('-test-selection'):
    canonical_builder = args.builder[:args.builder.rfind("-test-selection")]

  payload = {
      'graph': {
          'name':
              DECISION_GRAPH_NAME,
          'stages': [{
              'stage': {
                  'id':
                      STAGE_ID % (args.test_target, args.change, args.patchset),
                  'name':
                      STAGE_NAME,
              },
              'execution_options': {
                  'location': LOCATION_ENUM,
                  'address': STAGE_SERVICE_GSLB,
                  'prepare': True,
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
              'id': STAGE_ID % (args.test_target, args.change, args.patchset),
              'name': STAGE_NAME,
          },
          'input': [{
              'checks': [{
                  'identifier': {
                      'luci_test': {
                          'project': PROJECT,
                          'branch': BRANCH,
                          'builder': canonical_builder,
                          'test_suite': args.test_target,
                      }
                  },
                  'run': {
                      'luci_test': {
                          'build_id': args.build_id
                      }
                  },
              }],
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
  # Read the API key from the specified file
  api_key = read_api_key(args.api_key_file)

  request_with_key = '%s?key=%s' % (API_URL, api_key)
  response_data = fetch_api_data(url=request_with_key, json=payload)

  if response_data:
    print("API Response:")
    print(response_data)
    sys.exit(0)  # Return 0 if everything works fine
  else:
    print("Failed to fetch data from the API.")
    sys.exit(1)  # Return a non-zero code if there is an error
