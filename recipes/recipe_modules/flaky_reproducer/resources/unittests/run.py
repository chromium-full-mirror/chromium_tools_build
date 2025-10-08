#!/usr/bin/env vpython3
# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import os
from pathlib import Path
import sys

from coverage import Coverage
from coverage.execfile import PyRunner


def main():
  THIS_DIR = Path(__file__).absolute().parent
  os.chdir(THIS_DIR)

  sys.path.append(str(THIS_DIR.parent))
  os.environ['COVERAGE_RUN'] = 'true'

  cov = Coverage(data_file=None, omit=['./*.py'])

  runner = PyRunner(['unittest', 'discover'], as_module=True)
  runner.prepare()

  cov.start()
  try:
    runner.run()
  finally:
    cov.stop()
    if cov.report(show_missing=True) < 100:
      print('FATAL: unittests cover less than 100% of libs.')
      return 1  # pylint: disable=lost-exception

  return 0


if __name__ == '__main__':
  sys.exit(main())
