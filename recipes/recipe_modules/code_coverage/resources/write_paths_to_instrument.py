#!/usr/bin/env python3
# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Writes an input file for the partial code coverage compile wrapper.

https://chromium.googlesource.com/chromium/src/+/refs/heads/main/docs/clang_code_coverage_wrapper.md
"""

from __future__ import annotations

import argparse
import logging
import os
import sys


def _make_argument_parser(*args, **kwargs):
  parser = argparse.ArgumentParser(*args, **kwargs)
  parser.add_argument(
    '--write-to', required=True, help='path to input file to be written'
  )
  parser.add_argument(
    '--src-path', required=True, help='absolute path to checkout'
  )
  parser.add_argument(
    '--build-path', required=True, help='absolute path to build directory'
  )
  parser.add_argument(
    'sources',
    nargs='*',
    help='paths of source files to instrument relative to '
    '--src-path, with platform-specific path separator.',
  )
  return parser


def _rebase_paths(src_path, build_path, path_list):
  """Rebase elements of path_list to be relative to build_path.

  Each path in path_list must be relative to src_path.

  Returns a new list of paths that use os-native separators.
  """
  result = []
  for p in path_list:
    p = os.path.join(src_path, p)
    p = os.path.relpath(p, build_path)
    # normpath removes redundancy and on Windows converts '/' to '\'.
    p = os.path.normpath(p)
    result.append(p)
  return result


def main():
  desc = (
    'make the paths to the given source files relative to the build dir '
    'and write to a file'
  )
  parser = _make_argument_parser(description=desc)
  params = parser.parse_args()

  with open(params.write_to, 'w') as out_file:
    rebased_paths = _rebase_paths(
      params.src_path, params.build_path, params.sources
    )
    contents = '\n'.join(rebased_paths) + '\n'
    logging.info("Paths to instrument = %r" % rebased_paths)
    out_file.write(contents)


if __name__ == '__main__':
  sys.exit(main())
