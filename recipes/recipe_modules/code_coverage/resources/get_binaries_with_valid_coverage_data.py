#!/usr/bin/env python3
# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""This script gets binaries with valid coverage data for per-cl coverage."""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import subprocess
import sys


# TODO(crbug.com/929769): Remove this method when the fix is landed upstream.
def _get_binaries_with_coverage_data(profdata_path, llvm_cov_path, binaries,
                                     arch):
  """Gets binaries with valid coverage data.

  llvm-cov bails out with error message "No coverage data found" if an included
  binary does not exercise any instrumented file. The long-term solution should
  be making llvm-cov being able to proceed by ignoring the binaries without
  coverage data, however, for short-term, this method implements a solution to
  filter out binaries without coverage data by trying to invoke llvm-cov on each
  binary and decide if there is coverage data based on the return code and error
  message.

  This method is expected to run fast for per-cl coverage because only a small
  number of files are instrumented.
  """
  binaries_with_coverage_data = []
  for binary in binaries:
    cmd = [
        llvm_cov_path, 'export', '-summary-only',
    ]
    if arch:
      cmd.append('-arch=%s' % arch)
      # TODO(crbug.com/1068345): llvm-cov fails with a thread resource
      # unavailable exception if using more than one thread in iOS builder.
      if platform.system() == 'Darwin' and arch == 'x86_64':
        cmd.append('-num-threads=1')

    cmd.extend(['-instr-profile=%s' % profdata_path, binary])
    try:
      _ = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
    except subprocess.CalledProcessError as e:
      logging.error(e.output)
      # On Unix-like platforms, llvm-cov reports 'No coverage data found',
      # but on Windows it reports 'Could not load coverage information'.
      if e.returncode == 1 and ('no coverage data found' in e.output.lower() or
                                'could not load coverage information'
                                in e.output.lower()):
        logging.warning(
            '%s does not have coverage data, and will be excluded '
            'from exporting coverage metadata', binary)
        continue

      raise

    binaries_with_coverage_data.append(binary)

  return binaries_with_coverage_data


def _parse_args():
  parser = argparse.ArgumentParser()
  parser.usage = __doc__

  parser.add_argument(
      '--profdata-path',
      required=True,
      type=str,
      help='absolute path to the merged profdata')

  parser.add_argument(
      '--llvm-cov',
      required=True,
      type=str,
      help='absolute path to llvm-cov executable')

  parser.add_argument(
      '--output-json',
      required=True,
      type=str,
      help='absoluate path to the file that stores the output, and the format '
      'is a json list of absolute paths to the binaries with valid coverage '
      'data')

  parser.add_argument(
      'binaries',
      nargs='+',
      type=str,
      help='absolute path to binaries to generate the coverage for')

  parser.add_argument('--arch', type=str, help='architecture of binaries')

  return parser.parse_args()


def main():
  args = _parse_args()
  assert os.path.isfile(args.profdata_path), (
      '"%s" profdata file does not exist' % args.profdata_path)
  assert os.path.isfile(args.llvm_cov), '"%s" llvm_cov does not exist'

  binaries_with_coverage_data = _get_binaries_with_coverage_data(
      args.profdata_path, args.llvm_cov, args.binaries, args.arch)
  with open(args.output_json, 'w') as f:
    json.dump(binaries_with_coverage_data, f, separators=(',', ':'))


if __name__ == '__main__':
  sys.exit(main())
