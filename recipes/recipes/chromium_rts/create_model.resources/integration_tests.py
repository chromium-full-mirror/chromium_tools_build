#!/usr/bin/env python3
# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Runs integration tests.

Currently consists of a single test that ensures that `rts-chromium select`
works.
"""

import argparse
import contextlib
import os
import shutil
import subprocess
import sys
import tempfile


def main(raw_args):
  args = parse_args(raw_args)

  with temp_dir() as out_dir:
    # Mutate the checkout for the minimal amount of time.
    with code_change(args.chromium_checkout):
      subprocess.check_call(
        [
          args.rts_exec,
          'select',
          '-checkout',
          args.chromium_checkout,
          '-model-dir',
          args.model_dir,
          '-target-change-recall',
          '0.95',
          '-out',
          out_dir,
        ]
      )

    count = 0
    with open(os.path.join(out_dir, 'browser_tests.filter')) as f:
      for line in f:
        assert line.startswith('-'), line
        count += 1
    assert count > 10, count


def parse_args(raw_args):
  parser = argparse.ArgumentParser()
  parser.add_argument(
    '--rts-exec', required=True, help='Path to the RTS execuable.'
  )
  parser.add_argument(
    '--model-dir', required=True, help='Path to the RTS model.'
  )
  parser.add_argument(
    '--chromium-checkout',
    required=True,
    help='Path to the Chromium checkout. Will be mutated',
  )
  return parser.parse_args(raw_args)


@contextlib.contextmanager
def temp_dir():
  ret = tempfile.mkdtemp(prefix='rts-test')
  try:
    yield ret
  finally:
    shutil.rmtree(ret)


@contextlib.contextmanager
def code_change(checkout_dir):
  file_name = os.path.join(
    checkout_dir,
    'chrome',
    'browser',
    'ui',
    'zoom',
    'chrome_zoom_level_otr_delegate.cc',
  )
  assert os.path.isfile(file_name), file_name
  add_blank_line(file_name)
  try:
    git(checkout_dir, 'add', file_name)
    yield
  finally:
    git(checkout_dir, 'checkout', file_name)


def add_blank_line(file_name):
  with open(file_name, 'a') as f:
    f.write('\n')


def git(repo, *args):
  subprocess.check_call(['git', '-C', repo] + list(args))


if __name__ == '__main__':
  sys.exit(main(sys.argv[1:]))
