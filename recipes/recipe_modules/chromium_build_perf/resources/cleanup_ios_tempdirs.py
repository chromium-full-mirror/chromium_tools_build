#!/usr/bin/env python3
# Copyright (c) 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""This script removes temp directories for iOS simulators under /var/folders.
"""

import glob
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
      shutil.rmtree(d)


if '__main__' == __name__:
  sys.exit(main())
