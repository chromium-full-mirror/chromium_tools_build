#!/usr/bin/env python3
# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""This script merges all.json.gz from different metadata folders."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import zlib

_METADATA_FILENAME = 'all.json.gz'
_FILES_FIELD = 'files'


def _find_files(input_dirs):
  """Finds absolute paths of files from input folder."""
  metadata_files = []
  other_files = []
  other_file_names = set()
  for input_dir in input_dirs:
    for dir_path, dirs, files in os.walk(input_dir):
      # Note: There is a files_coverage subfolder storing shards of file
      # coverage data when there are > 1000 files in clang coverage metadata.
      # This is unlikely a problem here since this script is to work for
      # per-CL coverage only.
      assert not dirs, 'Cannot handle subdirs %s in dir %s' % (dirs, dir_path)
      for file_name in files:
        file_path = os.path.join(dir_path, file_name)
        if file_name == _METADATA_FILENAME:
          metadata_files.append(file_path)
        else:
          assert file_name not in other_file_names, (
            'Same file in different input: %s' % file_name
          )
          other_file_names.add(file_name)
          other_files.append(file_path)
  return metadata_files, other_files


def _read_metadata(metadata_file):
  with open(metadata_file, 'rb') as f:
    return json.loads(zlib.decompress(f.read()).decode())


def _verify_metadata(data):
  """Verifies the input metadata to ensure this script can handle it."""
  assert _FILES_FIELD in data, '"files" field must exist as a top level key!'

  # These fields might exist as top level key, but this script will ditch them
  # as the data is not useful for per-CL coverage.
  optional_fields = ['dirs', 'components', 'summaries']

  permitted_fields = optional_fields + [_FILES_FIELD]
  for field in data:
    assert field in permitted_fields, 'Cannot handle "%s" field!' % field


def _merge_metadata(metadata_files):
  """Merges metadata generated from multiple tools from a same build.

  The input is a list of metadata json files. The output is a python dict.
  Each input file and output confirms to coverage metadata definition:
  https://chromium.googlesource.com/infra/infra/+/refs/heads/main/appengine/findit/model/proto/code_coverage.proto.
  Per file coverage data is combined. Other data is ditched as not needed in
  per-CL coverage.
  The function has the following assumptions, otherwise an exception will be
  raised:
  - File coverage data are for different files.
  - 'files' top level fied must exist.
  - No 'file_shards' field.
  - No sub folders in input metadata folders.
  - No unknown top level fields.
  """
  merged = {_FILES_FIELD: []}
  seen_file_paths = set()
  for metadata_file in metadata_files:
    metadata = _read_metadata(metadata_file)
    _verify_metadata(metadata)

    current_paths = [
      file_entry['path'] for file_entry in metadata[_FILES_FIELD]
    ]
    assert not any(path in seen_file_paths for path in current_paths), (
      'Found duplicate paths in metadata. Current: %s. Seen: %s.'
      % (current_paths, seen_file_paths)
    )
    seen_file_paths.update(current_paths)

    merged[_FILES_FIELD].extend(metadata[_FILES_FIELD])

  return merged


def _argument_parser(*args, **kwargs):
  parser = argparse.ArgumentParser(*args, **kwargs)
  parser.add_argument(
    '--input-dirs',
    nargs='+',
    help='the source metadata folders to merge, at least one',
  )
  parser.add_argument(
    '--output-dir',
    required=True,
    type=str,
    help=(
      'absolute path to the directory to write the merged metadata, must exist'
    ),
  )
  return parser


def main():
  desc = (
    'merges "files" field from all.json.gz'
    'metadata files into 1. '
    'Copies other files to merged output.'
    'Raises exception if other files with the same '
    'file name exist in input dirs.'
  )
  parser = _argument_parser(description=desc)
  params = parser.parse_args()

  metadata_files, other_files = _find_files(params.input_dirs)

  merged_metadata = _merge_metadata(metadata_files)

  with open(os.path.join(params.output_dir, 'all.json.gz'), 'wb') as f:
    serialized_metadata = json.dumps(merged_metadata, separators=(',', ':'))
    f.write(zlib.compress(serialized_metadata.encode('utf-8')))

  for file_path in other_files:
    shutil.copy(file_path, params.output_dir)

  return 0


if __name__ == '__main__':
  sys.exit(main())
