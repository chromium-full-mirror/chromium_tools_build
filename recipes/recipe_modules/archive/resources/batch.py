#!/usr/bin/env vpython3
#
# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Wrapper for batch operations.

Builder may have a huge number of files to archive,
which may explode the step numbers that LUCI can accept.
This wrapper batch the step operations to background.
"""

from __future__ import annotations

import argparse
import os
import sys


def copy(base_dir, des_dir, input_file_list):
  with open(input_file_list, 'r') as f:
    for file in f:
      # Skip the empty line.
      if not file.strip():
        continue
      base_path = os.path.join(base_dir, file.strip())
      dst_path = os.path.join(des_dir, file.strip())
      dst_file_dir = os.path.dirname(dst_path)
      if not os.path.exists(dst_file_dir):
        os.makedirs(dst_file_dir, 0o777)
        print(f'Created {dst_file_dir}.')

      print(f'Hard-linking {base_path} to {dst_path}')
      os.link(base_path, dst_path)


def main(args):
  parser = argparse.ArgumentParser()
  subparsers = parser.add_subparsers()

  # Subcommand: copy
  subparser = subparsers.add_parser(
    'copy',
    help=(
      'Copy (actually hardlink) the files in the list to the destination '
      'folder, while keeping the relative path.'
    ),
  )
  subparser.add_argument(
    '-d', '--des-dir', required=True, help='Path to the destination directory.'
  )
  subparser.add_argument(
    '-b', '--base-dir', required=True, help='Path to the source directory.'
  )
  subparser.add_argument(
    '-i', '--input-file-list', required=True, help='The input txt file list'
  )
  subparser.set_defaults(
    func=lambda opts: copy(opts.base_dir, opts.des_dir, opts.input_file_list)
  )

  opts = parser.parse_args(args)
  opts.func(opts)


if __name__ == '__main__':
  sys.exit(main(sys.argv[1:]))
