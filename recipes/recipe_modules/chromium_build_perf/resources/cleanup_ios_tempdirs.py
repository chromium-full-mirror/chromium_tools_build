#!/usr/bin/env python3
# Copyright (c) 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""This script removes temp directories for iOS simulators under /var/folders."""

from __future__ import annotations

import glob
import os
import shutil
import sys


# tempfile.gettempdir() doesn't return system tempdir from recipe.
# hardcoding the glob pattern starting with /var/folders.
patterns = [
  '/var/folders/**/com.apple.CoreSimulator.SimDevice.*',
  '/var/folders/**/*IBTOOLD*',
  '/var/folders/**/ibtoold*',
]


def main():
  for p in patterns:
    for d in glob.iglob(p, recursive=True):
      print('Removing ' + d)
      try:
        # Since shutil.rmtree() gets stuck on named pipe, it needs to use
        # os.remove(). https://github.com/python/cpython/issues/116401
        if os.path.isdir(d):
          shutil.rmtree(d)
        else:
          os.remove(d)
      except FileNotFoundError as e:
        print(e, file=sys.stderr)


if '__main__' == __name__:
  sys.exit(main())
