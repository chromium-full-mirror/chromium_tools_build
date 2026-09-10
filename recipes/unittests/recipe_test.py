#!/usr/bin/env python3
# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Runs simulation tests and lint on the recipes."""

import os
import subprocess
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RECIPES_PY = os.path.join(ROOT_DIR, 'recipes.py')

VERBOSE = '--verbose' in sys.argv or '-v' in sys.argv
VERBOSE_ARG = ['--verbose'] if VERBOSE else []

ALLOWED_ARGS = 1 if VERBOSE else 0

if len(sys.argv) > ALLOWED_ARGS + 1:
  print('It looks like you\'re manually invoking this test script. Please note')
  print('that this is just a wrapper to enable `git cl presubmit` testing.')
  print()
  print('To interact with the recipes, please use %s directly.' % RECIPES_PY)
  sys.exit(1)

MODULE_ALLOWLIST = ['attr']


def recipes_py(*args):
  subprocess.check_call(
    [
      os.path.join(ROOT_DIR, 'recipes.py'),
    ]
    + VERBOSE_ARG
    + list(args)
  )


recipes_py('test', 'run')

recipes_py('lint', *('-a' + m for m in MODULE_ALLOWLIST))
