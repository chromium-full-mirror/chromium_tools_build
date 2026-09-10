#!/usr/bin/env python3
# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""This script provides utility functions related to Gerrit.

One of the main functionalities is to interact with Gerrit REST APIs, and for
more details on the REST APIs of Gerrit, please refer to:
https://gerrit-review.googlesource.com/Documentation/rest-api.html.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request

# A fixed prefix in the http response.
_RESPONSE_PREFIX = ')]}\n'

# Number of times to retry a http request.
_HTTP_NUM_RETRY = 3


def fetch_files_content(
  host,
  project,
  change,
  patchset,
  file_paths,
  token_path=None,
):
  """Fetches file content for a list of files from Gerrit.

  Args:
    host (str): The url of the host.
    project (str): The project name.
    change (int): The change number.
    patchset (int): The patchset number.
    file_paths (list): A list of file paths that are relative to the checkout.
    token_path (Optional[str]): Path to file with oauth token, if applicable.

  Returns:
    A list of String where each one corresponds to the content of each file.
  """
  # Uses the Get Change API to get and parse the revision of the patchset.
  # https://gerrit-review.googlesource.com/Documentation/rest-api-changes.html#get-change.
  project_quoted = urllib.parse.quote(project, safe='')
  change_id = '%s~%d' % (project_quoted, change)

  url = 'https://%s/changes/%s?o=ALL_REVISIONS&o=SKIP_MERGEABLE' % (
    host,
    change_id,
  )
  headers = {}
  token = None
  if token_path:
    with open(token_path) as oauth_token_fd:
      token = oauth_token_fd.read()
      headers['Authorization'] = 'Bearer %s' % token
  request = urllib.request.Request(url=url, headers=headers)
  response = _retry_url_open(request)
  change_details = json.loads(response.read()[len(_RESPONSE_PREFIX) :])
  patchset_revision = None

  for revision, value in change_details['revisions'].items():
    if patchset == value['_number']:
      patchset_revision = revision
      break

  if not patchset_revision:
    raise RuntimeError(
      'Patchset %d is not found in the change descriptions returned by '
      'requesting %s.' % (patchset, url)
    )

  result = []
  for file_path in file_paths:
    content = _fetch_file_content(
      host, change_id, patchset_revision, file_path, token=token
    )
    if content:
      result.append(content)

  return result


def _fetch_file_content(host, change_id, revision, file_path, token=None):
  """Fetches file content for a single file from Gerrit.

  Args:
    host (str): The url of the host.
    change_id (str): '<project>~<numericId>'.
    revision (str): Identifier that uniquely identifies a revision of a change.
    file_path (str): File path that is relative to the checkout.
    token (Optional[str]): Oauth token string, if applicable.

  Returns:
    A string representing the file content.
  """
  # Uses the Get Content API to get the file content from Gerrit.
  # https://gerrit-review.googlesource.com/Documentation/rest-api-changes.html#get-content
  file_path = file_path.replace(os.sep, '/')
  quoted_file_path = urllib.parse.quote(file_path, safe='')
  url = 'https://%s/changes/%s/revisions/%s/files/%s/content' % (
    host,
    change_id,
    revision,
    quoted_file_path,
  )
  headers = {}
  if token:
    headers['Authorization'] = 'Bearer %s' % token
  request = urllib.request.Request(url=url, headers=headers)
  response = _retry_url_open(request)
  try:
    content = base64.b64decode(response.read())
    return content.decode()
  except UnicodeDecodeError:
    logging.error("Unable to decode content from gerrit")
    return


def _retry_url_open(url):
  """Retry version of urllib.request.urlopen.

  Args:
    url (str|urllib.request.Request): The URL or Request object.

  Returns:
    The response if status code is 200, otherwise, exception is raised.
  """
  if isinstance(url, urllib.request.Request):
    url_for_logging = url.get_full_url()
  else:
    url_for_logging = url
  logging.info('Sending request to Gerrit: %s', url_for_logging)
  tries = _HTTP_NUM_RETRY
  delay_seconds = 1
  while True:
    try:
      return urllib.request.urlopen(url)
    except urllib.error.URLError:
      if tries == 0:
        logging.error('Failed to open URL: %s', url_for_logging)
        raise

      time.sleep(delay_seconds)
      tries -= 1
      delay_seconds *= 2
