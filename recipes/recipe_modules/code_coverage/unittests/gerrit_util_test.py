#!/usr/bin/env vpython3
# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import unittest

import mock

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(
  0, os.path.abspath(os.path.join(THIS_DIR, os.pardir, 'resources'))
)

import gerrit_util


class GerritUtilTest(unittest.TestCase):
  @mock.patch('gerrit_util.urllib.request.urlopen')
  def test_fetch_file_content_from_gerrit(self, mock_urlopen):
    revisions = {
      'revisions': {'da745617c0329e2a5faf53cbd577047d789e909d': {'_number': 1}}
    }
    file_content = 'line one\nline two\n'

    mock_urlopen().getcode.side_effect = [404, 200, 404, 200]
    mock_urlopen().read.side_effect = [
      (')]}\n' + json.dumps(revisions)).encode(),
      base64.b64encode(file_content.encode()),
    ]

    result = gerrit_util.fetch_files_content(
      'chromium-review.googlesource.com',
      'chromium/src',
      123456,
      1,
      ['dir/test.cc'],
    )
    self.assertEqual([file_content], result)

  @mock.patch('gerrit_util.urllib.request.urlopen')
  def test_fetch_file_content_with_auth(self, mock_urlopen):
    revisions = {
      'revisions': {'da745617c0329e2a5faf53cbd577047d789e909d': {'_number': 1}}
    }
    file_content = 'line one\nline two\n'

    mock_urlopen().getcode.side_effect = [200, 200]
    mock_urlopen().read.side_effect = [
      (')]}\n' + json.dumps(revisions)).encode(),
      base64.b64encode(file_content.encode()),
    ]

    with tempfile.NamedTemporaryFile() as token_file:
      token = 'deadbeef'
      token_file.write(token.encode())
      token_file.flush()

      result = gerrit_util.fetch_files_content(
        'chromium-review.googlesource.com',
        'chromium/src',
        123456,
        1,
        ['dir/test.cc'],
        token_path=token_file.name,
      )

    self.assertEqual([file_content], result)
    self.assertEqual(
      mock_urlopen.call_args[0][0].get_header('Authorization'),
      f'Bearer {token}',
    )


if __name__ == '__main__':
  unittest.main()
