# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Script to clean up __jacoco_sources.json files."""

from __future__ import annotations

import argparse
import logging
import os
import shutil
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
    description='Clean up JaCoCo sources JSON files'
  )
  parser.add_argument(
    '--sources-json-dir',
    required=True,
    type=str,
    help='absolute path to the directory to traverse'
    '*__jacoco_sources.json files',
  )
  parser.add_argument(
    '--java-coverage-dir',
    type=str,
    help='absolute path to the directory to store Java coverage data',
  )
  params = parser.parse_args(args=args)

  if not os.path.isdir(params.sources_json_dir):
    parser.error('%s is not an existing directory' % params.sources_json_dir)

  return params


def main():
  params = _parse_args(sys.argv[1:])
  sources_json_files = generator.get_files_with_suffix(
    params.sources_json_dir, generator.SOURCES_JSON_FILES_SUFFIX
  )
  logging.info('Found __jacoco_sources.json files: %s', str(sources_json_files))

  for sources_json_file in sources_json_files:
    os.remove(sources_json_file)

  if params.java_coverage_dir and os.path.isdir(params.java_coverage_dir):
    shutil.rmtree(params.java_coverage_dir)

  logging.info('Cleaning up finished')


if __name__ == '__main__':
  logging.basicConfig(
    format='[%(asctime)s %(levelname)s] %(message)s', level=logging.INFO
  )
  sys.exit(main())
