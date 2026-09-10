#!/usr/bin/env vpython3
# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

import mock

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(
  0, os.path.abspath(os.path.join(_THIS_DIR, os.pardir, 'resources'))
)

import query_cq_flakes


class QueryCQFlakesTest(unittest.TestCase):
  @mock.patch('query_cq_flakes.urllib.request.urlopen')
  @mock.patch('query_cq_flakes.urllib.request.Request')
  def test_basic(self, mock_url_request, mock_url_open):
    input_file = tempfile.NamedTemporaryFile(mode='w')
    output_file = tempfile.NamedTemporaryFile()
    input_json = {
      'project': 'chromium',
      'bucket': 'try',
      'builder': 'linux-rel',
      'tests': [
        {
          'step_ui_name': 'browser_tests (with patch)',
          'test_name': 'foo.bar',
        }
      ],
    }
    output_json = {
      'flakes': [
        {
          'test': {
            'step_ui_name': 'browser_tests (with patch)',
            'test_name': 'foo.bar',
          },
          'affected_gerrit_changes': [123],
        }
      ]
    }

    json.dump(input_json, input_file)
    input_file.flush()
    mock_url_open.return_value.read.return_value = json.dumps(
      output_json
    ).encode()

    query_cq_flakes.query_and_write_flakes(input_file.name, output_file.name)
    output_file.flush()

    self.assertEqual(
      'https://findit-for-me.appspot.com/_ah/api/findit/v1/get_cq_flakes',
      mock_url_request.call_args[1]['url'],
    )
    self.assertDictEqual(output_json, json.load(output_file))


if __name__ == '__main__':
  unittest.main()
