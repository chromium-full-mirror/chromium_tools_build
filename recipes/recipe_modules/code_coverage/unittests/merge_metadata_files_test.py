#!/usr/bin/env vpython3
# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import copy
import json
import os
import sys
import unittest
import zlib

import mock
from mock import mock_open

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(
  0, os.path.abspath(os.path.join(THIS_DIR, os.pardir, 'resources'))
)

import merge_metadata_files

_CORRECT_METADATA = {
  'files': [
    {
      'path': 'path/to/file.cc',
      'lines': [
        {'first': 83, 'last': 86, 'count': 4},
        {'first': 93, 'last': 102, 'count': 9},
      ],
    }
  ],
  'summaries': {'some': 'summaries'},
}

_ANOTHER_METADATA = {
  'files': [
    {
      'path': 'path/to/java_file.java',
      'lines': [
        {'first': 123, 'last': 456, 'count': 2},
      ],
    }
  ],
}

_ANOTHER_METADATA_DUPLICATED_PATH = {
  'files': [
    {
      'path': 'path/to/file.cc',
      'lines': [
        {'first': 123, 'last': 456, 'count': 2},
      ],
    }
  ],
}

_MERGED_METADATA = {
  'files': [
    {
      'path': 'path/to/file.cc',
      'lines': [
        {'first': 83, 'last': 86, 'count': 4},
        {'first': 93, 'last': 102, 'count': 9},
      ],
    },
    {
      'path': 'path/to/java_file.java',
      'lines': [
        {'first': 123, 'last': 456, 'count': 2},
      ],
    },
  ]
}


class MergeMetadataFilesTest(unittest.TestCase):
  @mock.patch('os.walk')
  def test_find_files(self, mock_walk):
    mock_walk.side_effect = [
      [
        (
          ('metadata_c'),
          [],
          [
            'all.json.gz',
            'coverage.json',
            'coverage0.json',
            'coverage_raw.json',
            'index.html',
            'llvm_cov.stderr.log',
          ],
        )
      ],
      [(('metadata_java'), [], ['all.json.gz', 'coverage.xml'])],
      [(('metadata_js'), [], ['all.json.gz'])],
    ]
    metadata_files, other_files = merge_metadata_files._find_files(
      ['input_dir_1', 'input_dir_2', 'input_dir_3']
    )
    expected_metadata_files = [
      'metadata_c/all.json.gz',
      'metadata_java/all.json.gz',
      'metadata_js/all.json.gz',
    ]
    expected_other_files = [
      'metadata_c/coverage.json',
      'metadata_c/coverage0.json',
      'metadata_c/coverage_raw.json',
      'metadata_c/index.html',
      'metadata_c/llvm_cov.stderr.log',
      'metadata_java/coverage.xml',
    ]
    self.assertEqual(metadata_files, expected_metadata_files)
    self.assertEqual(other_files, expected_other_files)

  @mock.patch('os.walk')
  def test_find_files_raise_when_subdir(self, mock_walk):
    mock_walk.side_effect = [
      [
        (
          ('metadata_c'),
          ['file_shards'],
          [
            'all.json.gz',
            'coverage.json',
            'coverage0.json',
            'coverage_raw.json',
            'index.html',
            'llvm_cov.stderr.log',
          ],
        ),
        (
          ('metadata_c/file_shards'),
          [],
          [
            'files_2.json',
            'files_1.json',
          ],
        ),
      ],
    ]
    with self.assertRaises(AssertionError) as ex:
      _1, _2 = merge_metadata_files._find_files(
        ['input_dir_1', 'input_dir_2', 'input_dir_3']
      )

    self.assertEqual(
      str(ex.exception),
      "Cannot handle subdirs ['file_shards'] in dir metadata_c",
    )

  @mock.patch('os.walk')
  def test_find_files_raise_when_same_filename_in_other_files(self, mock_walk):
    mock_walk.side_effect = [
      [
        (
          ('metadata_c'),
          [],
          [
            'all.json.gz',
            'coverage.json',
            'coverage0.json',
            'coverage_raw.json',
            'index.html',
            'llvm_cov.stderr.log',
          ],
        )
      ],
      [(('metadata_java'), [], ['all.json.gz', 'coverage.json'])],
    ]
    with self.assertRaises(AssertionError) as ex:
      _1, _2 = merge_metadata_files._find_files(
        ['input_dir_1', 'input_dir_2', 'input_dir_3']
      )

    self.assertEqual(
      str(ex.exception), 'Same file in different input: coverage.json'
    )

  def test_metadata_verify_field_correct(self):
    merge_metadata_files._verify_metadata(_CORRECT_METADATA)

  def test_metadata_no_files_field(self):
    metadata = copy.deepcopy(_CORRECT_METADATA)
    del metadata['files']
    with self.assertRaises(AssertionError) as ex:
      merge_metadata_files._verify_metadata(metadata)
    self.assertEqual(
      str(ex.exception), '"files" field must exist as a top level key!'
    )

  def test_metadata_unexpected_field(self):
    metadata = copy.deepcopy(_CORRECT_METADATA)
    metadata['unknown'] = {'some': 'content'}
    with self.assertRaises(AssertionError) as ex:
      merge_metadata_files._verify_metadata(metadata)
    self.assertEqual(str(ex.exception), 'Cannot handle "unknown" field!')

  def test_read_data(self):
    file_data = zlib.compress(json.dumps(_CORRECT_METADATA).encode())
    with mock.patch(
      'builtins.open', mock.mock_open(read_data=file_data)
    ) as mock_file:
      metadata = merge_metadata_files._read_metadata('filename')
      self.assertEqual(metadata, _CORRECT_METADATA)
    mock_file.assert_called_with('filename', 'rb')

  @mock.patch('merge_metadata_files._verify_metadata')
  @mock.patch('merge_metadata_files._read_metadata')
  def test_merge_metadata(self, mock_read, _):
    mock_read.side_effect = [
      _CORRECT_METADATA,
      _ANOTHER_METADATA,
    ]
    merged = merge_metadata_files._merge_metadata(
      ['metadata_dir_1/all.json.gz', 'metadata_dir_2/all.json.gz']
    )
    self.assertEqual(merged, _MERGED_METADATA)

  @mock.patch('merge_metadata_files._verify_metadata')
  @mock.patch('merge_metadata_files._read_metadata')
  def test_merge_metadata_duplicate_paths(self, mock_read, _):
    mock_read.side_effect = [
      _CORRECT_METADATA,
      _ANOTHER_METADATA_DUPLICATED_PATH,
    ]
    with self.assertRaises(AssertionError) as ex:
      merge_metadata_files._merge_metadata(
        ['metadata_dir_1/all.json.gz', 'metadata_dir_2/all.json.gz']
      )
    self.assertEqual(
      str(ex.exception),
      "Found duplicate paths in metadata. "
      "Current: ['path/to/file.cc']. Seen: {'path/to/file.cc'}.",
    )


if __name__ == '__main__':
  unittest.main()
