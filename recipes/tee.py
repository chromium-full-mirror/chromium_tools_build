#!/usr/bin/env python3
# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
from __future__ import print_function

import sys
import subprocess


def print_usage(err_msg):
  print(err_msg, file=sys.stderr)
  sys.exit('Usage: tee.py [file1 ...] -- command [arg1 ...]')


def main():
  try:
    idx = sys.argv.index('--')
  except ValueError:
    print_usage('Separator -- not found')

  cmd = sys.argv[idx + 1 :]
  files = sys.argv[1:idx]
  if not cmd:
    print_usage('Subcommand not specified')

  pipe = subprocess.Popen(cmd, stdout=subprocess.PIPE)
  outfiles = [open(f, 'w') for f in files]

  while True:
    buf = pipe.stdout.read(1024).decode('latin1')
    if buf == '':
      break

    sys.stdout.write(buf)
    for f in outfiles:
      f.write(buf)

  return_code = pipe.wait()

  for f in outfiles:
    f.close()

  return return_code


if __name__ == '__main__':
  sys.exit(main())
