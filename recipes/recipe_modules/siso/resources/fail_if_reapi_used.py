#!/usr/bin/env python3
# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import argparse
import json
import sys


def main():
  parser = argparse.ArgumentParser(
    description='Checks siso_metrics.json and fails if remote execution was used.'
  )
  parser.add_argument(
    '--siso_metrics_path',
    required=True,
    help='Path to the siso_metrics.json file.',
  )
  args = parser.parse_args()

  with open(args.siso_metrics_path, 'r', encoding='utf-8') as f:
    for line_number, line in enumerate(f, start=1):
      metric = json.loads(line)
      if metric.get('is_remote') or metric.get('cached'):
        out = metric.get('output')
        rule = metric.get('rule')
        print(
          f"Error: Remote execution found. rule: {rule}, output: {out}, line: {line_number}"
        )
        return 1
    return 0


if __name__ == '__main__':
  sys.exit(main())
