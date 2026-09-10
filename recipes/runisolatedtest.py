#!/usr/bin/env python3
# Copyright (c) 2012 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""A tool to run a chrome test executable directly, or in isolated mode.

TODO(maruel): This script technically needs to die and be replaced by running
all the tests always isolated even when not run on Swarming. This will take a
while.
"""

import logging
import optparse
import os
import subprocess
import sys


USAGE = (
  '%s [options] /full/path/to/test.exe -- [original test command]'
  % os.path.basename(sys.argv[0])
)

LINUX_ISOLATE_ENABLED_TESTS = set(
  (
    'base_unittests',
    'browser_tests',
    'interactive_ui_tests',
    'net_unittests',
    'unit_tests',
  )
)

# TODO(maruel): Not enabled because of lack of XCode support and missing
# dependencies for more complex tests.
MAC_ISOLATE_ENABLED_TESTS = set()

WIN_ISOLATE_ENABLED_TESTS = set(
  (
    'base_unittests',
    'browser_tests',
    'interactive_ui_tests',
    'net_unittests',
    'unit_tests',
  )
)

ISOLATE_ENABLED_BUILDERS = {
  # CI linux
  'Linux Tests': LINUX_ISOLATE_ENABLED_TESTS,
  # CI mac
  'Mac10.6 Tests (1)': MAC_ISOLATE_ENABLED_TESTS,
  'Mac10.7 Tests (1)': MAC_ISOLATE_ENABLED_TESTS,
  # CI win
  'Win7 Tests (1)': WIN_ISOLATE_ENABLED_TESTS,
  'Win10 Tests x64': WIN_ISOLATE_ENABLED_TESTS,
  # Try Server
  'linux_rel': LINUX_ISOLATE_ENABLED_TESTS,
  'mac_rel': MAC_ISOLATE_ENABLED_TESTS,
  'win_rel': WIN_ISOLATE_ENABLED_TESTS,
}


def should_run_as_isolated(builder_name, test_name):
  logging.info('should_run_as_isolated(%s, %s)' % (builder_name, test_name))
  return test_name in ISOLATE_ENABLED_BUILDERS.get(builder_name, [])


def run_command(command):
  """Inspired from chromium_utils.py's RunCommand()."""
  print('\n' + subprocess.list2cmdline(command))
  sys.stdout.flush()
  sys.stderr.flush()
  return subprocess.call(command)


def run_test_isolated(isolate_script, test_exe, original_command):
  """Runs the test under `isolate run`.

  It compensates for discrepancies between sharding_supervisor.py arguments and
  run_test_cases.py arguments.

  The isolate file must be alongside the test executable, with the same
  name and the .isolate extension.
  """
  isolate_file = os.path.splitext(test_exe)[0] + '.isolate'

  if not os.path.exists(isolate_file):
    logging.error('No isolate file %s', isolate_file)
    return 1

  isolate_command = [
    isolate_script,
    'run',
    '-isolate',
    isolate_file,
    # Print info log lines, so `isolate` prints the path to
    # the binary it's about to run, http://crbug.com/311625
    '-verbose',
  ]

  # Start setting the test specific options.
  isolate_command.append('--')
  isolate_command.append('--no-cr')
  for arg in original_command:
    if arg.startswith(('--gtest_filter', '--gtest_output', '--test-launcher')):
      isolate_command.append(arg)

  return run_command(isolate_command)


def main(argv):
  option_parser = optparse.OptionParser(USAGE)
  option_parser.add_option(
    '--test_name', default='', help='The name of the test'
  )
  option_parser.add_option(
    '--builder_name',
    default='',
    help='The name of the builder that created thistest',
  )
  option_parser.add_option(
    '--checkout_dir',
    help='Checkout directory, used to locate the swarm_client scripts.',
  )
  option_parser.add_option(
    '-v',
    '--verbose',
    action='count',
    default=0,
    help='Use to increase log verbosity. Can be passed '
    'in multiple times for more detailed logs.',
  )

  options, args = option_parser.parse_args(argv)

  test_exe = args[0]
  original_command = args[1:]

  # Initialize logging.
  level = [logging.ERROR, logging.INFO, logging.DEBUG][min(2, options.verbose)]
  logging.basicConfig(
    level=level,
    format='%(asctime)s %(filename)s:%(lineno)-3d %(levelname)s %(message)s',
    datefmt='%y%m%d %H:%M:%S',
  )

  if should_run_as_isolated(options.builder_name, options.test_name):
    logging.info('Running test in isolate mode')
    # Search first in luci-go
    isolate_script = os.path.join(
      options.checkout_dir, 'src', 'tools', 'luci-go', 'isolate'
    )

    return run_test_isolated(isolate_script, test_exe, original_command)

  logging.info('Running test normally')
  return run_command(original_command)


if '__main__' == __name__:
  sys.exit(main(None))
