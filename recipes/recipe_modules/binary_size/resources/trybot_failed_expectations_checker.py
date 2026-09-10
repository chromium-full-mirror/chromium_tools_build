#!/usr/bin/env python3
# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Checks for expectation failures stamp files created by enabling the gn arg
check_android_configuration. This is used by the size trybot to monitor
expectation file regressions."""

from __future__ import annotations

import argparse
import json
import os
import shutil


def _ClearExpectationsDir(dir_path):
  shutil.rmtree(dir_path, ignore_errors=True)


def _CheckExpectationsDir(dir_path):
  failed_expectations = []
  if os.path.exists(dir_path):
    for failed_expectation_file in os.listdir(dir_path):
      with open(os.path.join(dir_path, failed_expectation_file)) as f:
        fail_msg = f.read()
      if fail_msg:
        failed_expectations.append(fail_msg)
  return {
    'success': len(failed_expectations) == 0,
    'failed_messages': failed_expectations,
  }


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument(
    '--clear-expectations',
    action='store_true',
    help='Delete expectations files so they dont clober a new build.',
  )
  parser.add_argument(
    '--check-expectations',
    action='store_true',
    help='Check for the existance of failed expecation files after a build.',
  )
  parser.add_argument(
    '--output-directory', required=True, help='E.g. out/Release'
  )
  parser.add_argument(
    '--results-path', help='Output path for the trybot result .json file.'
  )
  args = parser.parse_args()

  expectations_dir = os.path.join(args.output_directory, 'failed_expectations')

  if args.check_expectations:
    if not args.results_path:
      parser.error(
        '--results-path is required when passing --check-expectations'
      )
    result = _CheckExpectationsDir(expectations_dir)
    with open(args.results_path, 'w') as f:
      json.dump(result, f)

  if args.clear_expectations:
    _ClearExpectationsDir(expectations_dir)


if __name__ == '__main__':
  main()
