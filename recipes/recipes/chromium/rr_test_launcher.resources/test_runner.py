# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Script to run the tests using the rr tool on swarming testing bots."""

import argparse
import logging
import subprocess
import os
import sys

MAX_RUNS = 5
TRACE_DIR_PREFIX = 'trace_dir_'


def parse_args(args):

  def list_of_tests(arg):
    return arg.split(',')

  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument(
      '--test-names',
      type=list_of_tests,
      help="The name of failing test to be reproduced")

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


def main(args):
  """Entrypoint for the execution of a strategy on a swarming task."""
  args = parse_args(args)
  logging.getLogger().setLevel(logging.INFO)

  if not args.test_names:
    raise Exception("No test input for this test runner")

  for test_name in args.test_names:
    for i in range(MAX_RUNS):
      # TODO(jiesheng): Replace the hard-coded test command based on test type.
      trace_dir_name = TRACE_DIR_PREFIX + str(i)
      cmd = [
          'vpython3', 'third_party/blink/tools/run_web_tests.py', '-t',
          'Release', '--no-retry-failures',
          '--wrapper=rr_tool/bin/rr record --output-trace-dir={0}'.format(
              trace_dir_name), test_name
      ]
      run_cmd(cmd, '../')

  # TODO(jiesheng): Select one pass and all failure traces, upload those
  # traces here with the source code and generate the final report back to
  # main script.


if __name__ == '__main__':
  main(sys.argv[1:])
