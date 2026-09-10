#!/usr/bin/env vpython
# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Script to fetch paths of jacoco and jar files for java code coverage"""

from __future__ import annotations

import argparse
import json
import logging
import sys

import generate_coverage_metadata_for_java as generator


def _parse_args(args):
  """Parses the arguments.

  Args:
    args: The passed arguments.

  Returns:
    The parsed arguments as parameters.
  """
  parser = argparse.ArgumentParser(
    description='Get jacoco and jar files for java'
  )

  parser.add_argument(
    '--sources-json-dir',
    required=True,
    type=str,
    help='absolute path to the directory to traverse'
    '*__jacoco_sources.json files',
  )

  parser.add_argument(
    '--output-json',
    required=True,
    type=str,
    help='absolute path to file that stores the output of this script; format'
    'is a json list of absolute paths to all files required for java'
    'code coverage',
  )
  return parser.parse_args(args=args)


def main():
  params = _parse_args(sys.argv[1:])

  jar_files = []
  class_files = []
  sources_json_files = generator.get_files_with_suffix(
    params.sources_json_dir, generator.SOURCES_JSON_FILES_SUFFIX
  )

  for f in sources_json_files:
    with open(f) as json_file:
      json_file_data = json.load(json_file)
      class_files.extend(json_file_data['input_path'])

  jar_files = [
    f
    for f in class_files
    if not f.endswith(generator.DEVICE_CLASS_EXCLUDE_SUFFIX)
    and not f.endswith(generator.HOST_CLASS_EXCLUDE_SUFFIX)
  ]

  output_files = [sources_json_files] + [jar_files]
  logging.info('Found all files: %r', output_files)

  with open(params.output_json, 'w') as f:
    json.dump(sources_json_files + jar_files, f, separators=(',', ':'))


if __name__ == '__main__':
  logging.basicConfig(
    format='[%(asctime)s %(levelname)s] %(message)s', level=logging.INFO
  )
  sys.exit(main())
