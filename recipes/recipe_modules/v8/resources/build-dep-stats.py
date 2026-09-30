#!/usr/bin/env python3
# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

# /// script
# requires-python = '>=3.11,<3.12'
# dependencies = [
#   'numpy==1.23.5+chromium.4'
# ]
# ///

"""Compute some statistics over the build dependencies in v8.

Outputs json to the given file or stdout.
"""

from __future__ import annotations

import argparse
import json
import numpy
import os
import re
import subprocess
import sys


def parse_args():
  parser = argparse.ArgumentParser()
  parser.add_argument(
    '-C', '--build-dir', type=str, required=True, help='ninja build directory'
  )
  parser.add_argument(
    '-o', '--output', type=str, help='Output file (default: stdout)'
  )
  parser.add_argument(
    '-x',
    '--exclude',
    type=str,
    action='append',
    help='Add an exclude pattern (regex)',
  )
  parser.add_argument(
    '-v', '--verbose', action='store_true', help='Print much more information'
  )
  return parser.parse_args()


args = parse_args()


class Graph:
  def __init__(self):
    self.nodes = {}

  def get_or_create_node(self, label):
    if label not in self.nodes:
      self.nodes[label] = Node(label)
    return self.nodes[label]


class Node:
  def __init__(self, label):
    self.edges = []
    self.label = label

  def add_edge(self, dst):
    self.edges.append(dst)


def printv(line):
  if args.verbose:
    print(line)


def parse_ninja_deps(ninja_deps):
  g = Graph()
  current_target = None
  for line in ninja_deps.splitlines():
    line = line.rstrip()
    printv('line: ' + line)
    # Ignore empty lines
    if not line:
      current_target = None
      continue
    if line[0] == ' ':
      # New dependency
      if len(line) < 5 or line[0:4] != '    ' or line[5] == ' ':
        sys.exit('Lines must have no indentation or exactly four ' + 'spaces.')
      dep = g.get_or_create_node(line[5:])
      if current_target is None:
        sys.exit('Missing new target before dep')
      dep.add_edge(current_target)
      printv('New dep from ' + current_target.label + ' to ' + dep.label)
      continue
    # New target
    colon_pos = line.find(':')
    if colon_pos < 0:
      sys.exit('Unindented line must have a colon')
    if current_target is not None:
      sys.exit('Missing empty line before new target')
    current_target = g.get_or_create_node(line[0:colon_pos])
    printv('New target: ' + current_target.label)
  return g


def get_ninja_deps():
  cmd = ['ninja', '-C', args.build_dir, '-t', 'deps']
  printv('Executing: ' + (' '.join(cmd)))
  return subprocess.check_output(cmd, encoding='utf-8')


def sort_nodes(nodes):
  return sorted(nodes, key=lambda n: len(n.edges), reverse=True)


def get_stats(nodes):
  deps = [len(n.edges) for n in nodes]
  sorted_nodes = sort_nodes(nodes)
  top100_deps = [len(n.edges) for n in sorted_nodes[:100]]
  top200_deps = [len(n.edges) for n in sorted_nodes[:200]]
  top500_deps = [len(n.edges) for n in sorted_nodes[:500]]

  return {
    'num_files': len(nodes),
    'avg_deps': numpy.average(deps),
    'median_deps': numpy.median(deps),
    'top100_avg_deps': numpy.average(top100_deps),
    'top100_median_deps': numpy.median(top100_deps),
    'top200_avg_deps': numpy.average(top200_deps),
    'top200_median_deps': numpy.median(top200_deps),
    'top500_avg_deps': numpy.average(top500_deps),
    'top500_median_deps': numpy.median(top500_deps),
  }


def filter_excludes(nodes):
  patterns = [re.compile(x) for x in args.exclude or []]
  return [n for n in nodes if not any(r.search(n.label) for r in patterns)]


def main():
  ninja_deps = get_ninja_deps()
  g = parse_ninja_deps(ninja_deps)

  nodes = filter_excludes(g.nodes.values())

  data = get_stats(nodes)

  get_ext = lambda n: os.path.splitext(n.label)[1][1:]

  extensions = sorted(set(map(get_ext, nodes)))
  get_ext_nodes = lambda e: [n for n in nodes if get_ext(n) == e]
  data['by_extension'] = {e: get_stats(get_ext_nodes(e)) for e in extensions}

  print('Top 500 header files:')
  for i, n in enumerate(sort_nodes(get_ext_nodes('h'))[:500]):
    print(' [{:3d}]  {} ({} deps)'.format(i, n.label, len(n.edges)))

  json_str = json.dumps(data, indent=2, sort_keys=True)
  if args.output and args.output != '-':
    with open(args.output, 'w') as outfile:
      outfile.write(json_str + '\n')
  else:
    print(json_str)


if __name__ == '__main__':
  main()
