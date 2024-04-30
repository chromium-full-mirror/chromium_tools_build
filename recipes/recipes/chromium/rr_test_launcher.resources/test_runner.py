# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Script to run the tests using the rr tool on swarming testing bots."""

import argparse
import logging
import subprocess
import tarfile
import os
import platform
import sys

MAX_RUNS = 5
TRACE_DIR_PREFIX = 'trace_dir_'
TEST_RESULT_FILE = 'test_result'


def parse_args(args):

  def list_of_tests(arg):
    return arg.split(',')

  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument(
      '--test-names',
      type=list_of_tests,
      help="The name of failing test to be reproduced")
  parser.add_argument('--output-dir', help="The output dir of all test traces")

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

  if not args.test_names:
    raise Exception("No test input for this test runner")

  if not args.output_dir:
    raise Exception("No output dir for this test runner")

  for test_name in args.test_names:
    test_name_plain = sanitize_test_name(test_name, '_')
    for i in range(MAX_RUNS):
      # TODO(jiesheng): Replace the hard-coded test command based on test type.
      trace_dir_name = TRACE_DIR_PREFIX + str(i)
      cmd = [
          'vpython3', 'third_party/blink/tools/run_web_tests.py', '-t',
          'Release', '--no-retry-failures', '--driver-kill-timeout-secs=10',
          f'--isolated-script-test-output={TEST_RESULT_FILE}',
          f'--wrapper=rr_tool/bin/rr record '
          f'--output-trace-dir={trace_dir_name}', test_name
      ]
      run_cmd(cmd, '../')

      # Pack the test trace and upload the trace and test result file to output
      # dir.
      result = run_cmd(['rr_tool/bin/rr', 'pack', trace_dir_name], '../')
      if result == 0:
        with tarfile.open('trace.tar', 'w') as tar:
          tar.add(f'../{trace_dir_name}')
        tar.close()
        os.renames('trace.tar',
                   f'{args.output_dir}/{test_name_plain}/{str(i)}/trace.tar')
        os.renames(
            f'../{TEST_RESULT_FILE}', f'{args.output_dir}/{test_name_plain}/'
            f'{str(i)}/{TEST_RESULT_FILE}')
      else:
        logging.error('Result of running rr pack is %r', result)


if __name__ == '__main__':
  main(sys.argv[1:])
