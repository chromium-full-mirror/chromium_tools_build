# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Script to run the tests using the rr tool on swarming testing bots."""

import argparse
import json
import logging
import subprocess
import os
import pathlib
import platform
import sys

MAX_RUNS = 5
TRACE_DIR = 'trace_dir'
TEST_RESULTS_JSON = pathlib.Path('layout-test-results/full_results.json')
RR_PATH = os.path.abspath(os.path.join(os.getcwd(), '../../rr_tool/bin'))
TRACE_PATH = os.path.abspath(os.path.join(os.getcwd(), '../../', TRACE_DIR))
SOURCE_DATA = """[{{
"files": [
  {{
    "url": "https://chromium.googlesource.com/chromium/src/+/{0}/",
    "at": "{1}",
    "urlSuffix": "?format=TEXT"
  }}
],
"relevance": "Relevant"
}}]"""


def parse_args(args):
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument(
      '--test',
      '-t',
      required=True,
      action='append',
      help='The test list of '
      'failing tests to be reproduced.')
  parser.add_argument(
      '--output-dir', required=True, help='The output dir of all test traces.')
  parser.add_argument(
      '--git-revision',
      required=True,
      help='The git revision of the source code.')
  parser.add_argument('test_cmd', nargs='*', help='The test command list.')

  return parser.parse_args(args)


def run_cmd(cmd, cwd='./', env=None):
  """Run command locally.

  Args:
    cmd (list[str]): Sequence of cmd arguments.
    cwd (str): Change working directory, default is current directory.
  """
  logging.info('Running %r in %r', cmd, cwd)
  old_cwd = os.getcwd()
  os.chdir(cwd)
  try:
    with subprocess.Popen(cmd, env=env) as process:
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

  # Set up the docker image of pernosco. For details, see:
  # https://github.com/Pernosco/on-prem/blob/main/README.md#google-cloud
  run_cmd([
      'gcloud/bin/gcloud', 'auth', '-q', 'configure-docker',
      'us-west1-docker.pkg.dev'
  ], '../../')
  run_cmd(['pernosco/pernosco', '--gcloud', 'pull'], '../../')

  def _remove_old_trace_dir():
    run_cmd(['rm', '-rf', TRACE_DIR], '../../')

  for test_name in args.test:
    test_name_plain = sanitize_test_name(test_name, '_')
    if not test_name_plain:
      continue
    test_cmd = args.test_cmd
    test_cmd.append(test_name)

    found_failure = False
    found_pass = False
    for _ in range(MAX_RUNS):
      run_cmd(test_cmd)

      test_result = None
      with open(
          pathlib.Path(args.output_dir) / TEST_RESULTS_JSON,
          'r',
          encoding='utf-8') as f:
        results = json.load(f).get('num_failures_by_type', {})
        if results.get('PASS', 0) > 0:
          if found_pass:
            _remove_old_trace_dir()
            continue
          test_result = 'PASS'
        # If nothing passed, assume it was a failure (CRASH or FAIL)
        else:
          if found_failure:
            _remove_old_trace_dir()
            continue
          test_result = 'FAIL'

      # Add a new directory to the PATH
      env = os.environ.copy()
      env['PATH'] = f"{env['PATH']}{os.pathsep}{RR_PATH}"

      # Add the source code revision to the trace
      source_path = os.path.abspath(os.path.join(os.getcwd(), '..', '..'))
      with open(
          pathlib.Path(TRACE_PATH) / 'sources.user', 'w',
          encoding='utf-8') as f:
        source_data = json.loads(
            SOURCE_DATA.format(args.git_revision, source_path))
        json.dump(source_data, f, indent=2)

      # Run a subprocess with the modified environment
      run_cmd(['pernosco/pernosco', '--gcloud', 'build', TRACE_PATH], '../../',
              env)

      # Pack the test trace and upload the trace and test result file to output
      # dir.
      result = run_cmd(['rr_tool/bin/rr', 'pack', TRACE_DIR], '../../')
      if result == 0:
        run_cmd([
            'tar', '--exclude', './db*', '--use-compress-program=zstd', '-cf',
            'trace.tar', f'../../{TRACE_DIR}'
        ])
        output_path = f'{args.output_dir}/{test_name_plain}/{test_result}'
        os.renames('trace.tar', f'{output_path}/trace.tar')
        run_cmd([
            'split', '-b', '10G', f'{output_path}/trace.tar',
            f'{output_path}/trace'
        ])
        run_cmd(['rm', '-rf', f'{output_path}/trace.tar'])

        if test_result == 'PASS':
          found_pass = True
        else:
          found_failure = True
      else:
        logging.error('Result of running rr pack is %r', result)
      _remove_old_trace_dir()

      if found_failure and found_pass:
        break


if __name__ == '__main__':
  main(sys.argv[1:])
