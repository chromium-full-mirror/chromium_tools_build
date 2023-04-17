#!/usr/bin/env python3
# Copyright (c) 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""This script is used to detect cancellation of builds and run `goma_ctl.py ensure_stop`.

Run like
$ goma_canceller.py /path/to/goma_ctl.py

"""

import datetime
import signal
import subprocess
import sys
import time


def do_exit():
  subprocess.check_call(['python3', sys.argv[1], 'ensure_stop'])
  print('goma_canceller stopped goma')


def main():
  print('goma_canceller started')
  signal.signal(
      (
          signal.SIGBREAK  # pylint: disable=no-member
          if sys.platform.startswith('win') else signal.SIGTERM),
      lambda _signum, _frame: do_exit())

  # Wait goma is started in recipe.
  time.sleep(10)

  while True:
    time.sleep(1)
    # pylint: disable=subprocess-run-check
    p = subprocess.run(['python3', sys.argv[1], 'status'],
                       capture_output=True,
                       text=True)
    if p.returncode == 1:
      print('%s: goma_ctl status shows: %s' %
            (datetime.datetime.now(), p.stdout))
      break

  print('goma_canceller exiting')


if '__main__' == __name__:
  sys.exit(main())
