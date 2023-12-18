#!/usr/bin/env python3
# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Traverses the output dir and aggregates 'invalid_profiles.json' instances."""

import argparse
import json
import os
import sys

_INVALID_PROFILE_FILENAME = 'invalid_profiles.json'


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument('--root-dir', required=True, help='directory to traverse')
  params = parser.parse_args()

  # Record the names of the steps for which we had to exclude some profiles from
  # the merge due to corruption or malformedness.
  steps_with_failed_profiles = {}
  num_failed_profiles = 0
  for path, _dirs, files in os.walk(params.root_dir):
    if _INVALID_PROFILE_FILENAME not in files:
      continue

    # Note that the directory name is a modified version of the step
    # name.
    # We could pass the step name to the merge script, but seems this
    # should be enough for manual debugging.
    step_name = os.path.basename(path)
    try:
      with open(os.path.join(path, _INVALID_PROFILE_FILENAME)) as f:
        invalid_profiles = json.load(f)
        if invalid_profiles:
          steps_with_failed_profiles[step_name] = invalid_profiles
          num_failed_profiles += len(invalid_profiles)
    except ValueError:
      steps_with_failed_profiles[step_name] = '- BAD JSON FORMAT'

  if num_failed_profiles:
    result = {
        'total': num_failed_profiles,
        'failed profiles': steps_with_failed_profiles
    }
    json.dump(result, sys.stdout)


if __name__ == '__main__':
  sys.exit(main())
