#!/usr/bin/env python3
#
# Copyright 2015 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Wrapper to the legacy zip function, which stages files in a directory.
"""

import json
import os
import stat
import sys

# Add build/recipes, and build/scripts.
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(
    0,
    os.path.abspath(os.path.join(THIS_DIR, '..', '..', '..', '..', 'recipes')))
sys.path.insert(
    0,
    os.path.abspath(os.path.join(THIS_DIR, '..', '..', '..', '..', 'scripts')))

from common import chromium_utils

def main(argv):
  with open(argv[3], 'r') as f:
    zip_file_list = json.load(f)
  (zip_dir, zip_file) = chromium_utils.MakeZip(argv[1],
                                               argv[2],
                                               zip_file_list,
                                               argv[4],
                                               raise_error=True)
  chromium_utils.RemoveDirectory(zip_dir)
  if not os.path.exists(zip_file):
    raise Exception('Failed to make zip package %s' % zip_file)

  # Report the size of the zip file to help catch when it gets too big.
  zip_size = os.stat(zip_file)[stat.ST_SIZE]
  print('Zip file is %ld bytes' % zip_size)


if __name__ == '__main__':
  sys.exit(main(sys.argv))
