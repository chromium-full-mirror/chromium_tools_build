#!/usr/bin/env python3
#
# Copyright 2015 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Wrapper to the legacy zip function, which stages files in a directory."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys

# Add build/recipes, and build/scripts.
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(
  0, os.path.abspath(os.path.join(THIS_DIR, '..', '..', '..', '..', 'recipes'))
)
sys.path.insert(
  0, os.path.abspath(os.path.join(THIS_DIR, '..', '..', '..', '..', 'scripts'))
)

from common import chromium_utils


def main(argv):
  parser = argparse.ArgumentParser(description='Archive zipper tool.')
  parser.add_argument(
    '-o',
    '--output-dir',
    required=True,
    help='Absolute path to the directory in which the archive is to be '
    'created.',
  )
  parser.add_argument(
    '-a', '--archive-name', required=True, help='The archive name.'
  )
  parser.add_argument(
    '-j',
    '--json-file-list',
    required=True,
    help='A json file listing the files that must be added to the archive.',
  )
  parser.add_argument(
    '-f',
    '--file-relative-dir',
    required=True,
    help='Absolute path to the directory containing the files '
    'and subdirectories in the file_list.',
  )
  parser.add_argument(
    '--no-root-dir',
    default=False,
    action='store_true',
    help='Whether the archive should not contain root directory.',
  )
  parser.add_argument(
    '-lz',
    '--lzma-sdk-dir',
    default=None,
    help='Path to the bin directory of the lzma SDK which '
    'contains the 7z executable.',
  )
  args = parser.parse_args()

  with open(args.json_file_list, 'r') as f:
    zip_file_list = json.load(f)
  (zip_dir, zip_file) = chromium_utils.MakeZip(
    output_dir=args.output_dir,
    archive_name=args.archive_name,
    file_list=zip_file_list,
    file_relative_dir=args.file_relative_dir,
    no_root_dir=args.no_root_dir,
    lzma_sdk_bin=args.lzma_sdk_dir,
    raise_error=True,
  )
  chromium_utils.RemoveDirectory(zip_dir)
  if not os.path.exists(zip_file):
    raise Exception('Failed to make zip package %s' % zip_file)

  # Report the size of the zip file to help catch when it gets too big.
  zip_size = os.stat(zip_file)[stat.ST_SIZE]
  print('Zip file is %ld bytes' % zip_size)


if __name__ == '__main__':
  sys.exit(main(sys.argv))
