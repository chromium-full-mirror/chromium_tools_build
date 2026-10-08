# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Get native libraries from .isolate file for an android target."""

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
    description='Get native libraries for an android target'
  )
  parser.add_argument(
    '--chromium-output-dir',
    required=True,
    help='absolute path to the chromium output directory',
  )
  parser.add_argument(
    '--isolate-target',
    required=True,
    help='isolate target name of the target to find libraries for',
  )
  parser.add_argument(
    '--output-json',
    required=True,
    help='absoluate path to the file that stores the output, and the format '
    'is a json list of absolute paths to relevant binaries',
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


def _get_library_paths(chromium_output_dir, isolate_target):
  """Gets all native library artifact paths for a target.

  When the linker is configured with use_mold = true (which uses
  --separate-debug-file), files under lib.unstripped lack __llvm_covmap and are
  resolved to the main library in chromium_output_dir if it exists.

  Args:
    chromium_output_dir: absolute path to the chromium output directory.
    isolate_target: isolate target name of the test target.

  Returns:
    A list of all found paths.
  """
  use_mold = _uses_mold(chromium_output_dir)
  isolated_path = os.path.join(
    chromium_output_dir, '%s.isolate' % isolate_target
  )
  with open(isolated_path) as f:
    # This is a list of relative paths from build output.
    raw_isolated_paths = json.load(f).get('variables', {}).get('files', [])
    all_isolated_paths = [os.path.normpath(path) for path in raw_isolated_paths]

  is_wanted = lambda path: (
    path.startswith('lib.unstripped/') and path.endswith('.so')
  )

  wanted_paths = [path for path in all_isolated_paths if is_wanted(path)]
  resolved_paths = []
  for lib_path in wanted_paths:
    unstripped_full = os.path.join(chromium_output_dir, lib_path)
    if use_mold:
      main_lib_rel = os.path.relpath(lib_path, 'lib.unstripped')
      main_lib_full = os.path.join(chromium_output_dir, main_lib_rel)
      if os.path.exists(main_lib_full):
        resolved_paths.append(main_lib_full)
        continue
      logging.warning(
        'use_mold enabled, but main library path %s does not exist '
        'for %s; falling back to %s',
        main_lib_full,
        isolate_target,
        unstripped_full,
      )
    resolved_paths.append(unstripped_full)
  return resolved_paths


def main():
  params = _parse_args(sys.argv[1:])
  paths = _get_library_paths(params.chromium_output_dir, params.isolate_target)
  logging.info(
    'For %s, found native libraries files: %r' % (params.isolate_target, paths)
  )

  with open(params.output_json, 'w') as f:
    json.dump(paths, f, separators=(',', ':'))


if __name__ == '__main__':
  logging.basicConfig(
    format='[%(asctime)s %(levelname)s] %(message)s', level=logging.INFO
  )
  sys.exit(main())
