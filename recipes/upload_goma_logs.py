#!/usr/bin/env vpython3
# Copyright (c) 2015 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""upload goma related logs."""

from __future__ import print_function

import argparse
import collections
import json
import os
import sys

import goma_utils


def main():
  parser = argparse.ArgumentParser(description='Upload build related logs')
  parser.add_argument(
      '--log-url-json-file',
      help='If set, the script will write url of uploaded '
      'log visualizer.'
  )
  parser.add_argument(
      '--ninja-log-outdir',
      metavar='DIR',
      help='Directory that has .ninja_log file.'
  )
  parser.add_argument(
      '--ninja-log-compiler',
      metavar='COMPILER',
      help='compiler name used for the build.'
  )
  parser.add_argument(
      '--ninja-log-command-file',
      metavar='FILE',
      help='command line options of the build, which is '
      'written in the file.'
  )
  parser.add_argument(
      '--build-exit-status',
      type=int,
      metavar='EXIT_STATUS',
      help='build command exit status.'
  )

  parser.add_argument(
      '--gsutil-py-path',
      help='Specify path to gsutil.py script in depot_tools.'
  )

  parser.add_argument(
      '--build-id', default=0, type=int, help='unique ID of the current build'
  )
  parser.add_argument(
      '--build-step-name', default='', help='step name of the current build'
  )

  args = parser.parse_args()

  override_gsutil = None
  if args.gsutil_py_path:
    # Needs to add '--', otherwise gsutil options will be passed to gsutil.py.
    override_gsutil = ['python3', args.gsutil_py_path, '--']

  viewer_urls = {}

  ninja_log_command = '(unknown)'
  if args.ninja_log_command_file:
    # TODO(shinyak): Assuming file exists.
    with open(args.ninja_log_command_file, 'r') as f:
      ninja_log_command = f.read()

  if args.ninja_log_outdir:
    viewer_url = goma_utils.UploadNinjaLog(
        outdir=args.ninja_log_outdir,
        compiler=args.ninja_log_compiler,
        command=ninja_log_command,
        exit_status=args.build_exit_status,
        build_id=args.build_id,
        step_name=args.build_step_name,
        override_gsutil=override_gsutil
    )
    if viewer_url is not None:
      viewer_urls['ninja_log'] = viewer_url

  if args.log_url_json_file:
    with open(args.log_url_json_file, 'w') as f:
      f.write(json.dumps(viewer_urls))

  return 0


if '__main__' == __name__:
  sys.exit(main())
