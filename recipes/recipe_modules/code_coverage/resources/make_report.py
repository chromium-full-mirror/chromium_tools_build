#!/usr/bin/env python3
# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""This script generates an html code coverage report using llvm-cov."""

from __future__ import annotations

import argparse
import glob
import os
import sys

import reporter


def _make_report_argument_parser(*args, **kwargs):
  parser = argparse.ArgumentParser(*args, **kwargs)
  parser.add_argument(
    '--report-directory',
    required=True,
    help='dir to store the generated report in, must exist',
  )
  parser.add_argument(
    '--compilation-directory',
    required=True,
    help='dir used as a base for relative coverage mapping paths',
  )
  parser.add_argument(
    '--profdata-path', required=True, help='where the merged profdata is stored'
  )
  parser.add_argument(
    '--llvm-cov', required=True, help='path to llvm-cov executable'
  )
  parser.add_argument(
    '--binaries', nargs='+', help='the binaries to generate the report for'
  )
  parser.add_argument(
    '--sources', nargs='*', help='the source files to include in the report'
  )
  parser.add_argument('--arch', type=str, help='architecture of binaries')
  return parser


def main():
  desc = 'generate an html report for the coverage data in <--profdata-path>'
  parser = _make_report_argument_parser(description=desc)
  params = parser.parse_args()

  # Validate parameters
  if not os.path.exists(params.report_directory):
    raise RuntimeError(
      'Output directory %s must exist' % params.report_directory
    )

  if not os.path.isfile(params.llvm_cov) or not os.access(
    params.llvm_cov, os.X_OK
  ):
    raise RuntimeError('%s must exist and be executable' % params.llvm_cov)

  if not os.path.exists(params.profdata_path):
    raise RuntimeError('Input data %s missing' % params.profdata_path)

  reporter.generate_report(
    params.llvm_cov,
    params.profdata_path,
    params.report_directory,
    params.compilation_directory,
    params.binaries,
    params.sources,
    params.arch,
  )

  return 0


if __name__ == '__main__':
  sys.exit(main())
