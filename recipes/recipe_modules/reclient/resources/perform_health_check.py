#!/usr/bin/env python3
# Copyright (c) 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import argparse
import sys
import io

from os import path

FAILURE_STATUS = "1"


def check_for_reclient_fatals(log_dir):
  """Performs reclient health check by determining
  whether any FATAL log entries were produced
  during compilation

  Raises:
    Exception if FATAL log file is found in the log_dir
  """
  for file_name in [
    'bootstrap.FATAL',
    'reproxy.FATAL',
    'rewrapper.FATAL',
    'bootstrap.exe.FATAL',
    'reproxy.exe.FATAL',
    'rewrapper.exe.FATAL',
  ]:
    if path.exists(path.join(log_dir, file_name)):
      raise Exception("Found {} log file".format(file_name))

  print("no reclient's FATAL log entries found in %s" % log_dir)


def check_for_ip_timeouts(log_dir, build_exit_status):
  if build_exit_status != FAILURE_STATUS:
    print(
      "build_exit_status({}) != {}, skipping IP timeouts check".format(
        build_exit_status, FAILURE_STATUS
      )
    )
    return

  error_logs = [
    p
    for p in [
      path.join(log_dir, n) for n in ['reproxy.ERROR', 'reproxy.exe.ERROR']
    ]
    if path.exists(p)
  ]
  if not error_logs:
    print("no reproxy errors found")
    return

  with io.open(
    error_logs[0], mode='r', encoding='utf-8', errors='replace'
  ) as f:
    for l in [l.strip() for l in f.readlines()]:
      # TODO(b/233275188) replace with logic verifying LocalMetadata Stats once new entries are added
      if (
        'this build has encountered too many action input processing timeouts'
        in l
      ):
        raise Exception("The build failed early due to IP timeouts")


def main():
  parser = argparse.ArgumentParser(description='Performs reclient health check')

  parser.add_argument(
    '--reclient-log-dir',
    required=True,
    help='Path to the reclient log directory',
  )

  parser.add_argument(
    '--build-exit-status', required=True, help='Exit status of the build'
  )

  args = parser.parse_args()
  log_dir = args.reclient_log_dir

  try:
    check_for_reclient_fatals(log_dir)
  except Exception as err:
    print("There was a FATAL error during reclient execution: {}".format(err))
    return 1

  try:
    check_for_ip_timeouts(log_dir, args.build_exit_status)
  except Exception as err:
    print(err)
    return 1

  return 0


sys.exit(main())
