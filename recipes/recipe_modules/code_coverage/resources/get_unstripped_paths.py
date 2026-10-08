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


def _uses_mold(chromium_output_dir):
  """Returns True if GN configured the build with use_mold."""
  build_vars_path = os.path.join(chromium_output_dir, 'build_vars.json')
  if not os.path.exists(build_vars_path):
    return False
  with open(build_vars_path) as f:
    return bool(json.load(f).get('use_mold'))


def _get_all_paths(chromium_output_dir):
  """Gets all binary artifact paths for coverage.

  When the linker is configured with use_mold = true (which uses
  --separate-debug-file), files under lib.unstripped/exe.unstripped lack
  __llvm_covmap and are resolved to the main binary in chromium_output_dir if
  it exists.

  Args:
    chromium_output_dir: absolute path to the chromium output directory.

  Returns:
    A list of all found paths.
  """
  use_mold = _uses_mold(chromium_output_dir)
  search_dirs = [
    os.path.join(chromium_output_dir, 'lib.unstripped'),
    os.path.join(chromium_output_dir, 'exe.unstripped'),
  ]
  paths = []
  for search_dir in search_dirs:
    for dir_path, _, file_names in os.walk(search_dir):
      for file_name in file_names:
        unstripped_path = os.path.join(dir_path, file_name)
        if use_mold:
          rel_to_search = os.path.relpath(unstripped_path, search_dir)
          main_binary_path = os.path.join(chromium_output_dir, rel_to_search)
          if os.path.exists(main_binary_path):
            paths.append(main_binary_path)
            continue
          logging.warning(
            'use_mold enabled, but main binary path %s does not '
            'exist; falling back to %s',
            main_binary_path,
            unstripped_path,
          )
        paths.append(unstripped_path)
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
