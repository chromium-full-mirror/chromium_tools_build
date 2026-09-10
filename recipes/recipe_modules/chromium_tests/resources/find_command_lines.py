#!/usr/bin/env python3
# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Scans build output directory for .isolate files, extracts the
command lines, and returns the map of target_name -> command line.

Used to figure out what command lines to pass to swarming tasks.
"""

from __future__ import annotations

import argparse
import ast
import glob
import json
import os

parser = argparse.ArgumentParser()
parser.add_argument(
  '--build-dir',
  required=True,
  help='Path to a directory to search for *.isolate files',
)
parser.add_argument(
  '--output-json', required=True, help='File to dump JSON results into.'
)
parser.add_argument(
  '--rts-model',
  action='store_true',
  default=False,
  required=False,
  help='Find any rts command lines instead.',
)
args = parser.parse_args()

command_line_map = {}

for path in glob.glob(os.path.join(args.build_dir, '*.isolate')):
  target_name = os.path.splitext(os.path.basename(path))[0]
  with open(path) as fp:
    isolate = ast.literal_eval(fp.read())
    if args.rts_model:
      key = 'rts_command'
    else:
      key = 'command'
    cmd = isolate.get('variables', {}).get(key)
    if cmd is not None:
      command_line_map[target_name] = cmd

with open(args.output_json, 'w') as fp:
  json.dump(command_line_map, fp)
