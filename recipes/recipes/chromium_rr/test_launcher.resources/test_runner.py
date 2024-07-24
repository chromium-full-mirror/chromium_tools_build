# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Script to run the tests using the rr tool on swarming testing bots."""

import argparse
import logging
import subprocess
import os
import platform
import sys

MAX_RUNS = 5
TRACE_DIR = 'trace_dir'
TEST_RESULT_FILE = 'test_result'


def parse_args(args):
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument(
      '--test',
      '-t',
      required=True,
      action='append',
      help="The test list of "
      "failing tests to be reproduced.")
  parser.add_argument(
      '--output-dir', required=True, help="The output dir of all test traces")
  parser.add_argument('test_cmd', nargs='*', help="The test command list.")

  return parser.parse_args(args)


def run_cmd(cmd, cwd=None):
  """Run command locally.

  Args:
    cmd (list[str]): Sequence of cmd arguments.
    cwd (str): Change working directory.
  """
  logging.info('Running %r in %r', cmd, cwd)
  old_cwd = os.getcwd()
  os.chdir(cwd)
  try:
    with subprocess.Popen(cmd) as process:
      return process.wait()
  finally:
    os.chdir(old_cwd)


def sanitize_test_name(test_name, replacement):
  illegal_filename_chars = r'~#%&*{}\:<>?/|"'
  for char in illegal_filename_chars:
    test_name = test_name.replace(char, replacement)
  return test_name


def main(args):
  """Entrypoint for the execution of a strategy on a swarming task."""
  args = parse_args(args)
  logging.getLogger().setLevel(logging.INFO)

  if platform.system() != 'Linux':
    raise Exception('This test runner only supports Linux')

  for test_name in args.test:
    test_name_plain = sanitize_test_name(test_name, '_')
    test_cmd = args.test_cmd
    test_cmd.append(test_name)
    if not test_name_plain:
      continue
    for i in range(MAX_RUNS):
      run_cmd(test_cmd, '../')
      # Pack the test trace and upload the trace and test result file to output
      # dir.
      result = run_cmd(['rr_tool/bin/rr', 'pack', TRACE_DIR], '../')
      if result == 0:
        run_cmd([
            'tar', '--exclude', './db*', '--use-compress-program=zstd', '-cf',
            'trace.tar', f'../{TRACE_DIR}'
        ], './')
        os.renames('trace.tar',
                   f'{args.output_dir}/{test_name_plain}/{str(i)}/trace.tar')
        os.renames(
            f'../{TEST_RESULT_FILE}', f'{args.output_dir}/{test_name_plain}/'
            f'{str(i)}/{TEST_RESULT_FILE}')
      else:
        logging.error('Result of running rr pack is %r', result)
      # Remove the trace dir.
      run_cmd(['rm', '-rf', TRACE_DIR], '../')


if __name__ == '__main__':
  main(sys.argv[1:])
