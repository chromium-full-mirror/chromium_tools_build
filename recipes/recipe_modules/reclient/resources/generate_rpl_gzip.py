#!/usr/bin/env python3
# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import argparse
import gzip
import os
import shutil
import sys


def find_rpl_file(log_dir):
  for f in os.listdir(log_dir):
    # The suffix is 'rrpl' for 'reducedtext' log format.
    if f.endswith('.rrpl'):
      return os.path.join(log_dir, f)
  return None


def main():
  parser = argparse.ArgumentParser(
    description='Compresses the reproxy RPL log using Gzip'
  )

  parser.add_argument(
    '--reclient-log-dir',
    required=True,
    help='Path to the reclient log directory',
  )
  parser.add_argument(
    '--output-gzip-path', required=True, help='Path to the output gzip file.'
  )

  args = parser.parse_args()
  log_dir = args.reclient_log_dir
  rpl_path = find_rpl_file(log_dir)
  if rpl_path is None:
    raise RuntimeError('cannot find RPL file under %s' % log_dir)
  gz_path = args.output_gzip_path
  with open(rpl_path, 'rb') as f_in, gzip.open(gz_path, 'wb') as f_out:
    shutil.copyfileobj(f_in, f_out)

  print('RPL={} size={}'.format(rpl_path, os.path.getsize(rpl_path)))
  print('GZIP={} size={}'.format(gz_path, os.path.getsize(gz_path)))


if __name__ == '__main__':
  sys.exit(main())
