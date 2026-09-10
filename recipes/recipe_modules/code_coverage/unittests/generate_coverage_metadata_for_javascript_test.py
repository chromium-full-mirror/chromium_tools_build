#!/usr/bin/env vpython3
# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import os
import sys
import unittest

import mock

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(
  0, os.path.abspath(os.path.join(THIS_DIR, os.pardir, 'resources'))
)

import generate_coverage_metadata_for_javascript as generator
import repository_util


class GenerateCoverageMetadataForJavaScriptTest(unittest.TestCase):
  MOCK_LCOV_DATA = [
    "TN:",
    "SF:mydir/myfile1.js",
    "DA:1,0",
    "DA:2,0",
    "DA:3,4",
    "DA:4,4",
    "DA:5,4",
    "end_of_record",
    "TN:",
    "SF:mydir/myfile2.js",
    "DA:1,0",
    "DA:2,0",
    "DA:3,0",
    "DA:4,0",
    "DA:5,0",
    "end_of_record",
  ]

  MOCK_LINE_COLUMN_FORMAT_OUTPUT = (
    # Covered lines
    [
      {'count': 3, 'last': 3, 'first': 1},
      {'count': 1, 'last': 4, 'first': 4},
      {'count': 2, 'last': 6, 'first': 5},
      {'count': 0, 'first': 7, 'last': 7},
      {'count': 2, 'last': 9, 'first': 8},
      {'count': 3, 'last': 10, 'first': 10},
      {'count': 1, 'last': 11, 'first': 11},
    ],
    # Uncovered blocks
    [
      {'ranges': [{'last': 18, 'first': 16}], 'line': 6},
      {'ranges': [{'last': 17, 'first': 0}], 'line': 7},
      {'ranges': [{'last': 3, 'first': 0}], 'line': 8},
    ],
  )

  FILE_REVISIONS = {
    '//mydir/myfile1.js': ('hash1', 1234),
    '//mydir/myfile2.js': ('hash1', 1234),
  }

  COMPONENT_MAPPING = {'mydir': 'Test>Component'}

  @mock.patch.object(repository_util, 'AddGitRevisionsToCoverageFilesMetadata')
  @mock.patch.object(generator, '_get_raw_coverage_data')
  def test_generate_json_coverage_metadata(
    self, mock_get_raw_coverage_data, mock_add_get_revisions
  ):
    self.maxDiff = None
    mock_get_raw_coverage_data.return_value = self.MOCK_LCOV_DATA

    mock_add_get_revisions.return_value = self.FILE_REVISIONS

    expected_output = {
      'files': [
        {
          'path': '//mydir/myfile1.js',
          'lines': [
            {'first': 1, 'last': 2, 'count': 0},
            {'first': 3, 'last': 5, 'count': 4},
          ],
          'summaries': [{'name': 'line', 'total': 5, 'covered': 3}],
        },
        {
          'path': '//mydir/myfile2.js',
          'lines': [{'first': 1, 'last': 5, 'count': 0}],
          'summaries': [{'name': 'line', 'total': 5, 'covered': 0}],
        },
      ],
      'dirs': [
        {
          'dirs': [],
          'path': '//mydir/',
          'summaries': [{'covered': 3, 'total': 10, 'name': 'line'}],
          'files': [
            {
              'path': '//mydir/myfile1.js',
              'name': 'myfile1.js',
              'summaries': [{'covered': 3, 'total': 5, 'name': 'line'}],
            },
            {
              'path': '//mydir/myfile2.js',
              'name': 'myfile2.js',
              'summaries': [{'covered': 0, 'total': 5, 'name': 'line'}],
            },
          ],
        },
        {
          'dirs': [
            {
              'path': '//mydir/',
              'name': 'mydir/',
              'summaries': [{'covered': 3, 'total': 10, 'name': 'line'}],
            }
          ],
          'path': '//',
          'summaries': [{'covered': 3, 'total': 10, 'name': 'line'}],
          'files': [],
        },
      ],
      'summaries': [{'covered': 3, 'total': 10, 'name': 'line'}],
      'components': [
        {
          'dirs': [
            {
              'path': '//mydir/',
              'name': 'mydir/',
              'summaries': [{'covered': 3, 'total': 10, 'name': 'line'}],
            }
          ],
          'path': 'Test>Component',
          'summaries': [{'covered': 3, 'total': 10, 'name': 'line'}],
        }
      ],
    }

    actual_output = generator.generate_json_coverage_metadata(
      'coverage_dir', 'path_to_src', self.COMPONENT_MAPPING
    )
    self.assertDictEqual(expected_output, actual_output)

  def test_to_compressed_file_record_with_diff_mapping(self):
    diff_mapping = {
      'mydir/myfile1.js': {
        '2': [10, 'A line added by the patch.'],
        '3': [11, 'Another added line.'],
        '5': [12, 'One more line.'],
      }
    }

    records = generator._to_compressed_file_record(
      self.MOCK_LCOV_DATA, ['mydir/myfile1.js'], diff_mapping
    )

    expected_record = {
      'path': '//mydir/myfile1.js',
      'summaries': [
        {
          'covered': 2,
          'name': 'line',
          'total': 3,
        }
      ],
      'lines': [
        {
          'first': 10,
          'last': 10,
          'count': 0,
        },
        {
          'first': 11,
          'last': 12,
          'count': 4,
        },
      ],
    }

    self.maxDiff = None
    self.assertEqual(len(records), 1)
    self.assertDictEqual(expected_record, records[0])


if __name__ == '__main__':
  unittest.main()
