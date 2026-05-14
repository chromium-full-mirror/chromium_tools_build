#!/usr/bin/env python
# Copyright (c) 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""A tool to extract size information from a build.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys


READELF_LINE_RE = re.compile(r'''
    ^\s* \[\s*\d+\]  # [Nr]
    \s+  (\.\S*)     # Name
    \s+  \S+         # Type
    \s+  [a-f0-9]+   # Addr
    \s+  [a-f0-9]+   # Off
    \s+  ([a-f0-9]+) # Size
    \s+  .+$         # (several other columns)
''', flags=re.VERBOSE | re.MULTILINE)


def _get_elf_section_size_map(path_to_binary):
  """Parse raw readelf output and return a map {section name: size in bytes}."""
  output = subprocess.check_output(['readelf', '-S', '-W', path_to_binary],
                                   universal_newlines=True)

  elf_map = {}
  for m in READELF_LINE_RE.finditer(output):
    elf_map[m.group(1)] = int(m.group(2), 16)
  return elf_map


def get_elf_file_size(path):
  # The total size is the sum of the size of each section -- except the BSS
  # segment, which isn't actually stored in the file.
  elf_map = _get_elf_section_size_map(path)
  return sum(size for (name, size) in elf_map.items() if name != '.bss')


def get_directory_size(path):
  total = 0
  for root, _, files in os.walk(path):
    for f in files:
      total += os.path.getsize(os.path.join(root, f))
  return total


def get_size(path):
  if os.path.isdir(path):
    return get_directory_size(path)
  if path.endswith('.so'):
    return get_elf_file_size(path)
  return os.path.getsize(path)


def main(argv):
  """Print the size of files specified to it as loose args."""

  parser = argparse.ArgumentParser()
  parser.add_argument('--output', type=argparse.FileType('w'),
                      default=sys.stdout, help='path to the JSON output file')
  parser.add_argument('files', nargs='+')
  parser.add_argument('--base-dir', default='.')

  options = parser.parse_args(argv)

  sizes = {}
  for filename in options.files:
    sizes[filename] = get_size(os.path.join(options.base_dir, filename))

  json.dump(sizes, options.output)

  return 0

if '__main__' == __name__:
  sys.exit(main(sys.argv[1:]))
