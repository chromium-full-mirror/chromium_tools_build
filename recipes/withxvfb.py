#!/usr/bin/env python3
# Copyright (c) 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""A tool used to start an xvfb virtual X server.

This script is used by the buildbots. It must be run from the outer
build directory, e.g. chrome-release/build/.

For a list of command-line options, call this script with '--help'.
"""

import optparse
import os
import subprocess
import sys

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(
  0, os.path.abspath(os.path.join(THIS_DIR, os.pardir, 'scripts'))
)
sys.path.insert(
  0,
  THIS_DIR,
)

import bot_utils
import xvfb

USAGE = '%s [options] start|stop' % os.path.basename(sys.argv[0])


def main():
  """Entry point for withxvfb.py.

  This function:
    (1) Parses the command-line options.
    (2) Starts X
    (3) Runs the given command
    (4) Stops X

  Returns:
    Exit code for this script.
  """
  option_parser = optparse.OptionParser(usage=USAGE)

  option_parser.add_option(
    '--target', default='Release', help='build target (Debug or Release)'
  )
  option_parser.add_option('--build-dir', help='Chromium build directory')

  options, args = option_parser.parse_args()

  if len(args) == 0:
    raise Exception("Provide command to run")

  bin_dir = os.path.join(options.build_dir, options.target)

  xvfb.StartVirtualX(bin_dir)
  try:
    # Run the desired command with DISPLAY set
    subprocess.check_call(args)
  finally:
    xvfb.StopVirtualX()
  return 0


if '__main__' == __name__:
  sys.exit(main())
