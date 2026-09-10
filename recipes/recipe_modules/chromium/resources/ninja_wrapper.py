#!/usr/bin/env vpython3
# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""The wrapper runs ninja build command and provides detailed info of failure.

The wrapper would just run ninja command if -o (--ninja_info_output) argument
isn't provided. If the argument is given, wrapper runs ninja deps and graph
tool to get detailed info when build fails, and finally writes info to file.
The ninja build command should be the last argument after '--' flag.
The first argument of ninja command should be set as ninja's absolute path.
Ninja environment should be set before calling wrapper from recipe:
  with self.m.context(env=ninja_env):
    self.m.python(name, ninja_wrapper.py)

Example:
vpython3 ninja_wrapper.py \
  [--ninja_info_output file_name.json] \
  [--failure_output failure_output] \
  -- /absolute/path/to/ninja -C build/path build_target

The wrapper writes detailed info in JSON format:
{
"failures":[
  {
    "output_nodes": [...], // failed nodes.
    "rule": "CXX", // rule of failed edge.
    "output": "...", // ninja output of failed edge.
    "dependencies": [...], // dependencies of failed nodes.
  },
  ...
  ]
}

"""

from __future__ import annotations

from gevent import monkey

monkey.patch_all()

import argparse
import collections
import gevent
import json
import os
import re
import subprocess
import sys
import time

_COLLECT_DEPENDENCIES_RULES = ['CXX', 'CC']

_AUTO_GENERATED_RE = re.compile(r'^gen/|obj/')

_DEPS_RE = re.compile(r'^(.+): #deps (\d+), deps mtime \d+ \((\w+)\)$')
_DEPS_NOT_FOUND_RE = re.compile(r'^(.+): deps not found$')

_NODE_LABEL_RE = re.compile(r'^"([^"]+)" \[label="([^"]+)"\]$')
_EDGE_LABEL_RE = re.compile(r'^"([^"]+)" \[label="([^"]+)", shape=ellipse\]$')
# The first capturing group matches input node id, while the second matches
# output edge id. If non-capturing group matches, it means node is order-only.
_NODE_EDGE_RE = re.compile(
  (
    r'^"([^"]+)" -> "([^"]+)" '
    r'\[arrowhead=none(?: style=(dotted))?\]$'
  )
)
# The first capturing group matches input edge id, while the second matches
# output node id. This match happens when node has multiple input nodes.
_EDGE_NODE_RE = re.compile(r'^"([^"]+)" -> "([^"]+)"$')
# The first capturing group matches input node id, the second matches output
# node id, and the thrid matches edge's rule name. This match happens when
# edge has only one input node and output node.
_NODE_NODE_RE = re.compile(r'^"([^"]+)" -> "([^"]+)" \[label="([^"]+)"\]$')
_GRAPH_IGNORED_LINES = [
  'digraph ninja {',
  'rankdir="LR"',
  'edge [fontsize=10]',
  'node [fontsize=10, shape=box, height=0.25]',
  '}',
]

_RULE_RE = re.compile(r'^\[\d+/\d+\] (\S+)')
_FAILED_RE = re.compile(r'^FAILED: (.*)$')
_FAILED_END_RE = re.compile(r'^ninja: build stopped:.*')

_TIME_CMD = '/usr/bin/time'


class WarningCollector:
  def __init__(self):
    self._warnings = []

  def add(self, warning_info):
    self._warnings.append(warning_info)

  def get(self):
    return self._warnings


# TODO(yichunli): Improve by checking whether a file is in the build dir.
def is_auto_generated(file_name):
  return _AUTO_GENERATED_RE.match(file_name)


def prune_virtual_env():
  # Set by VirtualEnv, no need to keep it.
  os.environ.pop('VIRTUAL_ENV', None)

  # Set by VPython, if scripts want it back they have to set it explicitly.
  os.environ.pop('PYTHONNOUSERSITE', None)

  # Look for "activate_this.py" in this path, which is installed by VirtualEnv.
  # This mechanism is used by vpython as well to sanitize VirtualEnvs from
  # $PATH.
  os.environ['PATH'] = os.pathsep.join(
    [
      p
      for p in os.environ.get('PATH', '').split(os.pathsep)
      if not os.path.isfile(os.path.join(p, 'activate_this.py'))
    ]
  )


def run_ninja_tool(ninja_cmd, warning_collector):
  data = ''
  try:
    data = subprocess.check_output(
      ninja_cmd, stderr=subprocess.STDOUT, universal_newlines=True
    )
  except Exception as e:
    warning_collector.add('Exception occurs when running ninja tool: %r' % e)
  return data


class Node:
  """Represents a node in ninja build graph."""

  def __init__(self):
    # Node has at most one input edge.
    self.input_edge = None
    self.output_edges = []
    self.node_name = None


class Edge:
  """Represents an edge in ninja build graph."""

  def __init__(self, name):
    self.rule_name = name
    self.normal_input_nodes = []
    # When order-only dependencies are out of date, the output is not rebuilt
    # until they are built, but changes in order-only dependencies alone do not
    # cause the output to be rebuilt.
    self.order_only_input_nodes = []
    self.output_nodes = []


class Graph:
  """Parses output of ninja graph tool and saves the build graph."""

  def __init__(self, warning_collector):
    self.node_id_dict = collections.defaultdict(Node)
    self.node_name_dict = {}
    self.edge_dict = {}
    self.recorders = [
      (_NODE_LABEL_RE, self._record_node_label),
      (_EDGE_LABEL_RE, self._record_edge_label),
      (_EDGE_NODE_RE, self._record_edge_node),
      (_NODE_EDGE_RE, self._record_node_edge),
      (_NODE_NODE_RE, self._record_node_node),
    ]
    self.warning_collector = warning_collector

  def _record_node_label(self, node_id, node_name):
    """Records node id and its file name."""
    node = self.node_id_dict[node_id]
    node.node_name = node_name
    self.node_name_dict[node_name] = node

  def _record_edge_label(self, edge_id, edge_rule):
    """Records edge id and its rule name."""
    edge = Edge(edge_rule)
    self.edge_dict[edge_id] = edge

  def _record_edge_node(self, edge_id, node_id):
    """Records edge's output node and node's input edge."""
    edge = self.edge_dict.get(edge_id)
    if edge:
      output_node = self.node_id_dict[node_id]
      edge.output_nodes.append(output_node)
      output_node.input_edge = edge
    else:
      self.warning_collector.add(
        'Edge id does not exist in graph when calling '
        '_recording_edge_node func: %r' % edge_id
      )

  def _record_node_edge(self, node_id, edge_id, order_only):
    """Records node's output edge and edge's input node."""
    edge = self.edge_dict.get(edge_id)
    if edge:
      input_node = self.node_id_dict[node_id]
      input_node.output_edges.append(edge)
      if order_only:
        edge.order_only_input_nodes.append(input_node)
      else:
        edge.normal_input_nodes.append(input_node)
    else:
      self.warning_collector.add(
        'Edge id does not exist in graph when calling '
        '_recording_node_edge func: %r' % edge_id
      )

  def _record_node_node(self, node_input_id, node_output_id, edge_rule):
    """Records edge's rule name and its single input and output node."""
    edge = Edge(edge_rule)
    input_node = self.node_id_dict[node_input_id]
    output_node = self.node_id_dict[node_output_id]
    input_node.output_edges.append(edge)
    edge.normal_input_nodes.append(input_node)
    output_node.input_edge = edge

  @classmethod
  def build_graph(cls, ninja_graph_output, warning_collector):
    """Builds graph given the output of ninja graph tool."""
    graph = cls(warning_collector)
    lines = ninja_graph_output.splitlines()
    index = 0
    total_length = len(lines)
    while index < total_length:
      line = lines[index].strip()
      index += 1
      if not line:
        continue
      match = None
      for regex, recorder in graph.recorders:
        match = regex.match(line)
        if match:
          recorder(*match.groups())
          break
      if not match:
        if line not in _GRAPH_IGNORED_LINES:
          warning_collector.add(
            'Unknown line when parsing graph output: %r' % line
          )
    return graph

  def get_root_deps(self, node_names):
    """Gets source dependencies by checking root nodes in graph."""
    root_deps = collections.defaultdict(list)
    for node_name in node_names:
      visited_edges = set()
      node = self.node_name_dict.get(node_name)
      if not node:
        self.warning_collector.add(
          'Node name does not exist in graph when calling '
          'get_root_deps func: %r' % node_name
        )
        continue
      if not node.input_edge:
        # The node itself is root node.
        continue
      edge_list = [node.input_edge]
      visited_edges.add(node.input_edge)
      while edge_list:
        edge = edge_list.pop()
        for input_node in edge.normal_input_nodes:
          if not input_node.input_edge:
            if not is_auto_generated(input_node.node_name):
              # If a file is generated by GN instead of ninja,
              # it could be a root node in build graph.
              root_deps[node_name].append(input_node.node_name)
          elif input_node.input_edge not in visited_edges:
            edge_list.append(input_node.input_edge)
            visited_edges.add(input_node.input_edge)
    return root_deps


class DepsInfo:
  """Stores the deps information.

  Attributes:
    source_deps: a list of strings representing source files that
                failed node dependes on.
    auto_generated_deps: a list of strings representing auto-generated files
                that failed node dependens on.
  """

  def __init__(self):
    self.source_deps = []
    self.auto_generated_deps = []


def parse_ninja_deps(ninja_deps_output, warning_collector):
  """Parses the output of 'ninja -t deps failed_nodes'.

  Args:
    ninja_deps_output: A string of ninja deps tool output.
    warning_collector: A object recording warning info.

  Returns:
    deps: a dictionary whose key is name of failed node and value
          is DepsInfo object.
  """

  deps = {}
  lines = ninja_deps_output.splitlines()
  index = 0
  total_length = len(lines)
  while index < total_length:
    line = lines[index].strip()
    index += 1
    if not line:
      continue
    match = _DEPS_RE.match(line)
    if match:
      failed_node, deps_num, _ = match.groups()
      deps_num = int(deps_num)
      if not index + deps_num <= total_length:
        warning_collector.add(
          'Expect %d deps, but %d line(s) left.'
          % (deps_num, (total_length - index))
        )
      deps_info = DepsInfo()
      for dep in lines[index : index + deps_num]:
        dep = dep.strip()
        if not dep:
          warning_collector.add('Unexpected empty deps line')
          continue
        if _AUTO_GENERATED_RE.match(dep):
          deps_info.auto_generated_deps.append(dep)
        else:
          deps_info.source_deps.append(dep)

      deps[failed_node] = deps_info
      index += deps_num + 1
    else:
      match = _DEPS_NOT_FOUND_RE.match(line)
      if match:
        failed_node = match.group(1)
        deps_info = DepsInfo()
        deps[failed_node] = deps_info
      else:
        warning_collector.add(
          'Unknown line when parsing deps output: %r' % line
        )
  return deps


class NinjaBuildOutputStreamingParser:
  """Parses ninja's stdout of build command in streaming way."""

  def __init__(self, warning_collector):
    self.failed_target_list = []
    self._last_line = None
    self._failure_begins = False
    self._last_target = None
    self._warning_collector = warning_collector
    self.failure_outputs = ''

  def parse(self, line):
    line = line.strip()
    if self._failure_begins and self._last_target:
      if not _RULE_RE.match(line) and not _FAILED_END_RE.match(line):
        self._last_target['output'] += line + '\n'
        self.failure_outputs += line + '\n'
      else:
        # Output of failed edge ends, save its info.
        self._failure_begins = False
        self.failed_target_list.append(self._last_target)
    else:
      failed_nodes_match = _FAILED_RE.match(line)
      self._failure_begins = False
      if failed_nodes_match:
        # Get new failed edge when line begins with 'FAILED: ...'.
        self._failure_begins = True
        rule_match = _RULE_RE.match(self._last_line)
        if rule_match:
          target = {}
          target['rule'] = rule_match.group(1)
          nodes = failed_nodes_match.group(1)
          # TODO(yichunli): Update split function, if ninja gets updated
          # and separates nodes by other delimiters rather than space.
          target['output_nodes'] = [node for node in nodes.split(' ') if node]
          target['output'] = ''
          target['dependencies'] = []
          self._last_target = target
          self.failure_outputs += self._last_line + '\n' + line + '\n'
        else:
          self._warning_collector.add(
            'Unknown line when parsing ninja stdout: %r' % self._last_line
          )
    self._last_line = line


def get_detailed_info(
  ninja_path, build_path, failed_target_list, warning_collector
):
  """Gets detailed compile failure information from ninja stdout.

  Args:
    ninja_path: a string representing ninja path.
    build_path: a string representing chromium build directory.
    failed_target_list: a list of dict representing detailed failure information
    warning_collector: a object recording warning info.

  Returns:
    a json string representing detailed failure information:
      failures:
      {
        "failures":[
          {
            "output_nodes": ["node/name.o"],
            "rule": "CXX",
            "output": "stdout/stderr of build rule/edge",
            "dependencies": [...], //this field is empty
            for rules that are not CXX/CC
          },
          ...
        ]
      }
  """

  failed_nodes = []
  for target in failed_target_list:
    # Dependencies would be too much in other rules.
    if target['rule'] in _COLLECT_DEPENDENCIES_RULES:
      failed_nodes.extend(target['output_nodes'])
  if failed_nodes:
    failed_nodes = list(set(failed_nodes))
    deps_command = [ninja_path, '-C', build_path, '-t', 'deps'] + failed_nodes
    ninja_deps_output = run_ninja_tool(deps_command, warning_collector)
    deps_dict = parse_ninja_deps(ninja_deps_output, warning_collector)
    auto_generated_deps = []
    for deps in deps_dict.values():
      auto_generated_deps.extend(deps.auto_generated_deps)
    graph_dict = collections.defaultdict(list)
    if auto_generated_deps:
      graph_command = [
        ninja_path,
        '-C',
        build_path,
        '-t',
        'graph',
      ] + auto_generated_deps
      ninja_graph_output = run_ninja_tool(graph_command, warning_collector)
      graph = Graph.build_graph(ninja_graph_output, warning_collector)
      graph_dict = graph.get_root_deps(auto_generated_deps)
    for target in failed_target_list:
      for output_node in target['output_nodes']:
        deps_info = deps_dict.get(output_node)
        if deps_info:
          target['dependencies'].extend(deps_info.source_deps)
          for auto_generated_dep in deps_info.auto_generated_deps:
            target['dependencies'].extend(graph_dict[auto_generated_dep])
      target['dependencies'] = list(set(target['dependencies']))
  return {'failures': failed_target_list}


def parse_args(args):
  """Parse arguments."""
  parser = argparse.ArgumentParser()
  parser.add_argument(
    '-o', '--ninja_info_output', help='Optional. Save result in file.'
  )
  parser.add_argument(
    'ninja_cmd',
    nargs='+',
    help=(
      'Ninja build command, e.g., '
      '/absolute/path/to/ninja -C build/path build_target'
    ),
  )
  parser.add_argument(
    '--failure_output', help='Save output of failed build edges in file.'
  )
  parser.add_argument(
    '--no_prune_venv',
    action='store_true',
    help=(
      'Don\'t prune the virtual environment when calling ninja. '
      'This is a hack; don\'t use this unless you talk '
      'to infra-dev@chromium.org'
    ),
  )
  parser.add_argument(
    '-t',
    '--io_timeout',
    default=60 * 60 * 2,
    type=float,
    help=(
      'Seconds to wait for new output from ninja before killing it. '
      '0 = let ninja run forever'
    ),
  )
  parser.add_argument(
    '--resource_usage_output_file',
    help='Measure resources used to run command and save result in a file.',
  )

  options = parser.parse_args(args)
  return options


def get_ninja_build_path(ninja_cmd):
  """Parses the chromium build path from the ninja command

  Args:
    ninja_cmd: a string representing the full ninja command passed through argv.

  Returns:
    build_path: a string representing chromium build directory.
  """
  prev_cmd = None
  build_path = None
  for cmd in ninja_cmd:
    if prev_cmd == '-C':
      build_path = cmd
      break
    prev_cmd = cmd

  return build_path


def main(argv):
  options = parse_args(argv)
  ninja_cmd = options.ninja_cmd

  # NOTE: Based on related handling in depot_tools/gn.py.
  # Prune all evidence of VPython/VirtualEnv out of the environment. This means
  # that we 'unwrap' vpython VirtualEnv path/env manipulation. Invocations of
  # `python` from GN should never inherit the gn.py's own VirtualEnv. This also
  # helps to ensure that generated ninja files do not reference python.exe from
  # the VirtualEnv generated from depot_tools' own .vpython file (or lack
  # thereof), but instead reference the default python from the PATH.
  # TODO(martiniss): This is a terrible hack and needs to be removed. See
  # https://crbug.com/984451 for more information
  if not options.no_prune_venv:
    prune_virtual_env()

  if options.resource_usage_output_file:
    ninja_cmd = [
      _TIME_CMD,
      '--format={"ru_utime": %U}',
      '-o',
      options.resource_usage_output_file,
    ] + ninja_cmd

  if not options.ninja_info_output and options.io_timeout <= 0:
    # Options are set such that we don't need to intercept the stdout
    popen = subprocess.Popen(ninja_cmd, universal_newlines=True)
    return popen.wait()

  popen = subprocess.Popen(ninja_cmd, stdout=subprocess.PIPE, text=False)

  warning_collector = WarningCollector()
  if options.ninja_info_output:
    # Ninja outputs build process info to stdout whenever it fails or succeeds
    # Parse output nodes and edges into a graph
    ninja_parser = NinjaBuildOutputStreamingParser(warning_collector)
  else:
    ninja_parser = None

  def readline_greenlet():
    """Readline in a greenlet with a timeout.

    Returns:
      None if we timeout, a string representing the line we read otherwise.
    """
    try:
      return gevent.spawn(popen.stdout.readline).get(
        block=True, timeout=options.io_timeout
      )
    except gevent.Timeout:
      return None

  if options.io_timeout > 0:
    readline = readline_greenlet
  else:
    readline = popen.stdout.readline

  timed_out = False
  for stdout_line in iter(readline, b''):
    if stdout_line is None:
      timed_out = True
      break

    # The shell command may contain escape codes which do not parse as valid
    # utf-8, such as `\\xc3` (which appears as `\\\xc3` in the python byte
    # string). The shell accepts the extra `\` produced by GN, but python does
    # not. So we need to drop it here.
    stdout_bytes = bytearray(stdout_line)
    i = 0
    while i < len(stdout_bytes) - 2:
      if stdout_bytes[i] == ord('\\') and stdout_bytes[i + 1] > 127:
        del stdout_bytes[i : i + 1]
      else:
        i += 1

    stdout_line = stdout_bytes.decode('utf-8')
    print(stdout_line, end='')

    if ninja_parser:
      ninja_parser.parse(stdout_line)

  try:
    # Tidy up
    popen.stdout.close()
  except (IOError, RuntimeError):
    # If we timed out, stdout pipe is still locked which can throw an IOError,
    # and the greenlet is actually still alive reading from stdout,
    # which can throw a
    # "RuntimeError: reentrant call inside <_io.BufferedReader>"
    # See gevent/subprocess.py:communicate()
    pass

  if timed_out:
    # Give user feedback
    message = (
      'ninja_wrapper.py: Ninja command timed out due to not receiving '
      'output for {} seconds.\n'.format(options.io_timeout)
    )
    print(message)

    if options.failure_output:
      with open(options.failure_output, 'w') as fw:
        fw.write(message)

    return_code = 124

    # Debugging info for help diagnosing the timeout
    if sys.platform != 'win32':
      print('Showing ps auxwwf to help debug why ninja timed out')
      if sys.platform == 'darwin':
        subprocess.call(['ps', 'auxww'])
      else:
        subprocess.call(['ps', 'auxwwf'])
      print('End of ps auxwwf')

    popen.kill()
  else:
    return_code = popen.wait()

  if return_code and options.ninja_info_output:
    ninja_path = ninja_cmd[0]
    build_path = get_ninja_build_path(ninja_cmd)
    data = get_detailed_info(
      ninja_path, build_path, ninja_parser.failed_target_list, warning_collector
    )
    data['warnings'] = warning_collector.get()
    with open(options.ninja_info_output, 'w') as fw:
      json.dump(data, fw)

    if options.failure_output:
      with open(options.failure_output, 'a') as fw:
        if ninja_parser.failure_outputs:
          fw.write(ninja_parser.failure_outputs)
        else:
          fw.write(
            "Unrecognized failures, please check the original stdout instead."
          )

  return return_code


if __name__ == '__main__':
  sys.exit(main(sys.argv[1:]))
