# Copyright (c) 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import argparse
import subprocess
import sys


def ProcessOptions():
  """Process options from command line."""
  argparser = argparse.ArgumentParser(add_help=False)
  argparser.add_argument(
    '--binary-path', help='Absolute binary path to execute.'
  )
  argparser.add_argument('--folder', help='Folder to compress')
  argparser.add_argument('--output-file', help='Output file')
  argparser.add_argument(
    '--compression-algorithm', help='Optional. Set compression algorithm.'
  )
  argparser.add_argument(
    '--compression-level', help='Optional. Set compression level.'
  )
  argparser.add_argument('--block-size', help='Optional. Set block size.')
  args = argparser.parse_args()
  return args


def Mksquashfs(binary_path, args):
  invoke_args = [binary_path, args.folder, args.output_file]
  if args.compression_algorithm:
    invoke_args.extend(['-comp', args.compression_algorithm])
  if args.compression_level:
    invoke_args.extend(['-Xcompression-level', args.compression_level])
  if args.block_size:
    invoke_args.extend(['-b', args.block_size])
  subprocess.check_call(invoke_args)


def main():
  args = ProcessOptions()
  Mksquashfs(args.binary_path, args)


if '__main__' == __name__:
  sys.exit(main())
