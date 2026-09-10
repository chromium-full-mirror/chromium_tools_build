# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Script to get all Android/Fuchsia unstripped artifacts' paths."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys


def _parse_args(args):
  """Parses the arguments.

  Args:
    args: The passed arguments.

  Returns:
    The parsed arguments as parameters.
  """
  parser = argparse.ArgumentParser(
    description='Get all Android/Fuchsia unstripped artifacts paths'
  )
  parser.add_argument(
    '--chromium-output-dir',
    required=True,
    help='absolute path to the chromium output directory',
  )
  parser.add_argument(
    '--output-json',
    required=True,
    help='absoluate path to the file that stores the output, and the format '
    'is a json list of absolute paths to all unstripped artifacts',
  )
  params = parser.parse_args(args=args)

  if not os.path.isdir(params.chromium_output_dir):
    parser.error('%s is not existing directory' % params.chromium_output_dir)

  return params


def _get_all_paths(chromium_output_dir):
  """Gets all unstripped artifacts' paths.

  Args:
    chromium_output_dir: absolute path to the chromium output directory.

  Returns:
    A list of all found paths.
  """
  search_dirs = [
    os.path.join(chromium_output_dir, 'lib.unstripped'),
    os.path.join(chromium_output_dir, 'exe.unstripped'),
  ]
  paths = []
  for search_dir in search_dirs:
    for dir_path, _, file_names in os.walk(search_dir):
      for file_name in file_names:
        paths.append(os.path.join(dir_path, file_name))
  return paths


def main():
  params = _parse_args(sys.argv[1:])
  paths = _get_all_paths(params.chromium_output_dir)
  logging.info('Found all files: %r', paths)

  with open(params.output_json, 'w') as f:
    json.dump(paths, f, separators=(',', ':'))


if __name__ == '__main__':
  logging.basicConfig(
    format='[%(asctime)s %(levelname)s] %(message)s', level=logging.INFO
  )
  sys.exit(main())
