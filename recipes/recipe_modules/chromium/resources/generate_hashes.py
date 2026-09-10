#!/usr/bin/env python3
# Copyright 2015 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import argparse
import hashlib
import os
import sys


# This is what hashlib.algorithms returned in Python 2.
# Python 3's hashlib.algorithms_available returns a much longer list, and we do
# not need so many different hashes for each tarball.
HASHING_ALGORITHMS = ('md5', 'sha1', 'sha224', 'sha256', 'sha384', 'sha512')


def main(argv):
  parser = argparse.ArgumentParser()
  parser.add_argument('input_file', type=argparse.FileType('rb'))
  parser.add_argument('output_file', type=argparse.FileType('w'))
  args = parser.parse_args(argv)

  file_contents = args.input_file.read()

  hashes = []
  for alg in HASHING_ALGORITHMS:
    hashes.append(
      '%s  %s  %s'
      % (
        alg,
        getattr(hashlib, alg)(file_contents).hexdigest(),
        os.path.basename(args.input_file.name),
      )
    )

  args.output_file.write('\n'.join(hashes) + '\n')

  return 0


if __name__ == '__main__':
  sys.exit(main(sys.argv[1:]))
