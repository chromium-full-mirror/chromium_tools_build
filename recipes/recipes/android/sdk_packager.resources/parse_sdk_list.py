#!/usr/bin/env python3
# Copyright 2019 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Parses the output of `android sdk list` into a JSON.

The emitted JSON takes the following form:

  {
    'available': [
      {
        'name': '{package name}',
        'description': '{package description}',
        'version': '{package version}'
      },
      ... # additional available packages
    ],
    'installed': [
      {
        'name': '{package name}',
        'description': '{package description}',
        'version': '{package version}'
      },
      ... # additional installed packages
    ]
  }

If the provided input cannot be parsed, no JSON will be emitted.
"""

import argparse
import json
import os
import re
import sys

PACKAGE_LINE_RE = re.compile(
  r'^\s*(\S+)\s+(\S+)(?:\s*\(\+\d+\))?(?:\s*->\s*(\S+)(?:\s*\(\+\d+\))?)?(?:\s+(.*))?$'
)
PACKAGES_HEADER_RE = re.compile(r'^([a-z]+) packages:\s*$', flags=re.IGNORECASE)
SEPARATOR_RE = re.compile(r'^[\s-]+$')
UPDATES_HEADER_RE = re.compile(r'([a-z]+)?\s*updates:\s*$', flags=re.IGNORECASE)


def ParseSdkList(raw):
  available_packages = []
  installed_packages = []

  current_section = None

  for line in raw.splitlines():
    if not line.strip():
      continue

    if SEPARATOR_RE.match(line):
      continue

    m = PACKAGES_HEADER_RE.match(line)
    if m:
      header_name = m.group(1).lower()
      if header_name == 'available':
        current_section = available_packages
      elif header_name == 'installed':
        current_section = installed_packages
      else:
        print('Unrecognized header name: "%s"' % header_name)
        current_section = None
      continue

    m = UPDATES_HEADER_RE.match(line)
    if m:
      current_section = None
      continue

    if current_section is not None:
      m = PACKAGE_LINE_RE.match(line)
      if m:
        name = m.group(1)
        installed_ver = m.group(2)
        desc = (m.group(4) or '').strip() or None
        current_section.append(
          {
            'name': name,
            'description': desc,
            'version': installed_ver,
          }
        )
      else:
        print('Unrecognized package line: "%s"' % line)

  return {
    'available': available_packages,
    'installed': installed_packages,
  }


def main(raw_args):
  parser = argparse.ArgumentParser()
  parser.add_argument(
    '--raw-input',
    required=True,
    type=os.path.realpath,
    help='Path from which raw output from `android sdk list` will be read.',
  )
  parser.add_argument(
    '--json-output',
    required=True,
    type=os.path.realpath,
    help='Path to which the output JSON will be written.',
  )
  args = parser.parse_args(raw_args)

  with open(args.raw_input) as raw_input_file:
    raw = raw_input_file.read()

  parsed = ParseSdkList(raw)
  with open(args.json_output, 'w') as json_output_file:
    json.dump(parsed, json_output_file)

  return 0


if __name__ == '__main__':
  sys.exit(main(sys.argv[1:]))
