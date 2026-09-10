#!/usr/bin/env vpython3
# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import os
import sys
import tempfile
import textwrap
import time
import unittest
import unittest.mock

import ninja_wrapper

_NINJA_STDOUT_CXX_RULE = """[35792/53672] CXX successful/a.o
[35793/53672] CXX a.o b.o
FAILED: a.o b.o
failed edge output line 1
failed edge output line 2
failed edge output line 3
[35794/53672] CXX successful/b.o
[35795/53672] CXX c.o
FAILED: c.o
failed edge output line 1
failed edge output line 2
ninja: build stopped: subcommand failed."""


_NINJA_STDOUT_NON_CXX_RULE = """[2893/2900] LINK ./a.exe
FAILED: a.exe
failed edge output line 1
[2894/2900] AR ./b.o
FAILED: b.0
failed edge output line 1
failed edge output line 2
[2895/2900] STAMP ./c.o
FAILED: c.o
failed edge output line 1
failed edge output line 2
failed edge output line 3
[2898/2900] ACTION ./d.o
FAILED: d.o
failed edge output line 1
failed edge output line 2
failed edge output line 3
failed edge output line 4
ninja: build stopped: subcommand failed."""


_NINJA_STDOUT_MIXED_RULE = """[2893/2900] CXX ./a.o
FAILED: a.o
failed edge output line 1
[2894/2900] CXX ./b.o
FAILED: b.o
failed edge output line 1
failed edge output line 2
[2898/2900] ACTION ./c.o
FAILED: c.o
failed edge output line 1
failed edge output line 2
failed edge output line 3
ninja: build stopped: subcommand failed."""


_GRAPH_RETURN = """digraph ninja {
rankdir="LR"
node [fontsize=10, shape=box, height=0.25]
edge [fontsize=10]
"node_id1" [label="gen/b.cc"]
"edge_id1" [label="edge_rule1", shape=ellipse]
"edge_id1" -> "node_id1"
"edge_id1" -> "node_id2"
"node_id3" -> "edge_id1" [arrowhead=none]
"node_id6" -> "edge_id1" [arrowhead=none style=dotted]
"node_id2" [label="flag.h"]
"node_id4" -> "node_id2" [label="edge_rule2"]
"node_id4" [label="../../flag.py"]
"node_id3" [label="b.o"]
"node_id5" -> "node_id3" [label="edge_rule3"]
"node_id5" [label="../../b.cc"]
"node_id6" [label="../../order.h"]
}"""


_DEPS_RETURN = """a.o: #deps 4, deps mtime 1 (STALE)
    ../../base/a.cc
    ../../base/a.h
    gen/b.cc
    ../../build/bd.h

b.o: #deps 3, deps mtime 1 (VALID)
    ../../base/b.cc
    ../../base/b.h
    gen/b.cc

c.o: #deps 1, deps mtime 1 (STALE)
    ../../base/b.cc
"""


_DEPS_NOT_FOUND_RETURN = """a.o: deps not found
b.o: #deps 3, deps mtime 1 (VALID)
    ../../base/b.cc
    ../../base/b.h
    gen/b.cc

"""


_DEPS_ERROR_RETURN = """ninja: error: unknown target 'obj/e.o'"""


class NinjaWrapperTestCase(unittest.TestCase):
  def setUp(self):
    # Monkey patch sys.platform to trigger the unix-only debug printing, even
    # on windows.
    self.actual_platform = sys.platform
    sys.platform = 'linux2'

  def tearDown(self):
    sys.platform = self.actual_platform

  def testParseNinjaStdoutCXX(self):
    warning_collector = ninja_wrapper.WarningCollector()
    ninja_parser = ninja_wrapper.NinjaBuildOutputStreamingParser(
      warning_collector
    )
    for line in _NINJA_STDOUT_CXX_RULE.splitlines():
      ninja_parser.parse(line)
    expected_list = [
      {
        'output_nodes': ['a.o', 'b.o'],
        'rule': 'CXX',
        'output': textwrap.dedent("""\
                                  failed edge output line 1
                                  failed edge output line 2
                                  failed edge output line 3
                                  """),
        'dependencies': [],
      },
      {
        'output_nodes': ['c.o'],
        'rule': 'CXX',
        'output': textwrap.dedent("""\
                                  failed edge output line 1
                                  failed edge output line 2
                                  """),
        'dependencies': [],
      },
    ]
    self.assertListEqual(ninja_parser.failed_target_list, expected_list)
    self.assertListEqual(warning_collector.get(), [])

  def testParseMalformattedNinjaStdout(self):
    warning_collector = ninja_wrapper.WarningCollector()
    ninja_parser = ninja_wrapper.NinjaBuildOutputStreamingParser(
      warning_collector
    )
    malformatted_stdout = textwrap.dedent("""\
                                          [] CXX node1
                                          FAILED: node1
                                          output line
                                          [1/1] CXX node2""")
    for line in malformatted_stdout.splitlines():
      ninja_parser.parse(line)
    self.assertListEqual(ninja_parser.failed_target_list, [])
    expected_warning = [
      "Unknown line when parsing ninja stdout: '[] CXX node1'"
    ]
    self.assertListEqual(warning_collector.get(), expected_warning)

  def testParseDeps(self):
    warning_collector = ninja_wrapper.WarningCollector()
    target_dict = ninja_wrapper.parse_ninja_deps(
      _DEPS_RETURN, warning_collector
    )
    source_deps_a = ['../../base/a.cc', '../../base/a.h', '../../build/bd.h']
    auto_generated_deps_a = ['gen/b.cc']
    self.assertListEqual(target_dict['a.o'].source_deps, source_deps_a)
    self.assertListEqual(
      target_dict['a.o'].auto_generated_deps, auto_generated_deps_a
    )

    source_deps_b = ['../../base/b.cc', '../../base/b.h']
    auto_generated_deps_b = ['gen/b.cc']
    self.assertListEqual(target_dict['b.o'].source_deps, source_deps_b)
    self.assertListEqual(
      target_dict['b.o'].auto_generated_deps, auto_generated_deps_b
    )
    self.assertListEqual(warning_collector.get(), [])

  def testParseEmptyDeps(self):
    warning_collector = ninja_wrapper.WarningCollector()
    malformatted_deps = textwrap.dedent("""\
            a.o: #deps 2, deps mtime 1 (VALID)
            ../../a.cc

    """)
    target_dict = ninja_wrapper.parse_ninja_deps(
      malformatted_deps, warning_collector
    )
    self.assertListEqual(target_dict['a.o'].source_deps, ['../../a.cc'])
    expected_warning = ['Unexpected empty deps line']
    self.assertListEqual(warning_collector.get(), expected_warning)

  def testParseExceedLimitDeps(self):
    warning_collector = ninja_wrapper.WarningCollector()
    malformatted_deps = textwrap.dedent("""\
            a.o: #deps 2, deps mtime 1 (VALID)
            ../../a.cc
    """)
    target_dict = ninja_wrapper.parse_ninja_deps(
      malformatted_deps, warning_collector
    )
    self.assertListEqual(target_dict['a.o'].source_deps, ['../../a.cc'])
    expected_warning = ['Expect 2 deps, but 1 line(s) left.']
    self.assertListEqual(warning_collector.get(), expected_warning)

  def testParseUnknownDeps(self):
    warning_collector = ninja_wrapper.WarningCollector()
    malformatted_deps = 'malformatted deps'
    target_dict = ninja_wrapper.parse_ninja_deps(
      malformatted_deps, warning_collector
    )
    self.assertDictEqual(target_dict, {})
    expected_warning = [
      'Unknown line when parsing deps output: %r' % malformatted_deps
    ]
    self.assertListEqual(warning_collector.get(), expected_warning)

  def testParseNinjaDepsNotFound(self):
    warning_collector = ninja_wrapper.WarningCollector()
    target_dict = ninja_wrapper.parse_ninja_deps(
      _DEPS_NOT_FOUND_RETURN, warning_collector
    )
    source_deps_b = ['../../base/b.cc', '../../base/b.h']
    auto_generated_deps_b = ['gen/b.cc']

    self.assertListEqual(target_dict['a.o'].source_deps, [])
    self.assertListEqual(target_dict['a.o'].auto_generated_deps, [])
    self.assertListEqual(target_dict['b.o'].source_deps, source_deps_b)
    self.assertListEqual(
      target_dict['b.o'].auto_generated_deps, auto_generated_deps_b
    )
    self.assertListEqual(warning_collector.get(), [])

  def testParseMalformattedGraph(self):
    warning_collector = ninja_wrapper.WarningCollector()
    malformatted_graph = 'malformatted'
    ninja_wrapper.Graph.build_graph(malformatted_graph, warning_collector)
    self.assertListEqual(
      warning_collector.get(),
      ['Unknown line when parsing graph output: %r' % malformatted_graph],
    )

  def testParseNinjaGraph(self):
    warning_collector = ninja_wrapper.WarningCollector()
    graph = ninja_wrapper.Graph.build_graph(_GRAPH_RETURN, warning_collector)
    graph_dict = graph.get_root_deps(['gen/b.cc'])
    expected_dict = {'gen/b.cc': ['../../b.cc']}
    self.assertDictEqual(graph_dict, expected_dict)
    self.assertListEqual(warning_collector.get(), [])

  def testCheckAutoGeneratedTrue(self):
    file_name = 'gen/123123'
    self.assertTrue(ninja_wrapper.is_auto_generated(file_name))

  def testCheckAutoGeneratedFalse(self):
    file_name = '../../123123'
    self.assertFalse(ninja_wrapper.is_auto_generated(file_name))

  @unittest.mock.patch(
    'ninja_wrapper.run_ninja_tool', side_effect=[_DEPS_RETURN, _GRAPH_RETURN]
  )
  def testGetDetailedInfo(self, _):
    failed_target_list = [
      {
        'output_nodes': ['a.o', 'b.o'],
        'rule': 'CXX',
        'output': textwrap.dedent("""\
                                  failed edge output line 1
                                  failed edge output line 2
                                  failed edge output line 3
                                  """),
        'dependencies': [],
      },
      {
        'output_nodes': ['c.o'],
        'rule': 'CXX',
        'output': textwrap.dedent("""\
                                  failed edge output line 1
                                  failed edge output line 2
                                  """),
        'dependencies': [],
      },
      {
        'output_nodes': ['d.o'],
        'rule': 'LINK',
        'output': textwrap.dedent("""\
                                  failed edge output line 1
                                  failed edge output line 2
                                  """),
        'dependencies': [],
      },
    ]
    expected_deps1 = set(
      [
        '../../base/a.cc',
        '../../base/a.h',
        '../../build/bd.h',
        '../../base/b.cc',
        '../../base/b.h',
        '../../b.cc',
      ]
    )
    expected_deps2 = set(['../../base/b.cc'])
    expected_deps3 = set([])
    warning_collector = ninja_wrapper.WarningCollector()
    target_dict = ninja_wrapper.get_detailed_info(
      '', '', failed_target_list, warning_collector
    )
    self.assertSetEqual(
      set(target_dict['failures'][0]['dependencies']), expected_deps1
    )
    self.assertSetEqual(
      set(target_dict['failures'][1]['dependencies']), expected_deps2
    )
    self.assertSetEqual(
      set(target_dict['failures'][2]['dependencies']), expected_deps3
    )
    self.assertListEqual(warning_collector.get(), [])

  @unittest.mock.patch(
    'ninja_wrapper.run_ninja_tool', side_effect=['', _GRAPH_RETURN]
  )
  def testGetDetailedInfoErrorDeps(self, _):
    failed_target_list = [
      {
        'output_nodes': ['a.o', 'b.o'],
        'rule': 'CXX',
        'output': textwrap.dedent("""\
                                  failed edge output line 1
                                  failed edge output line 2
                                  failed edge output line 3
                                  """),
        'dependencies': [],
      },
      {
        'output_nodes': ['c.o'],
        'rule': 'CXX',
        'output': textwrap.dedent("""\
                                  failed edge output line 1
                                  failed edge output line 2
                                  """),
        'dependencies': [],
      },
    ]
    expected_deps1 = set([])
    expected_deps2 = set([])
    warning_collector = ninja_wrapper.WarningCollector()
    target_dict = ninja_wrapper.get_detailed_info(
      '', '', failed_target_list, warning_collector
    )
    self.assertSetEqual(
      set(target_dict['failures'][0]['dependencies']), expected_deps1
    )
    self.assertSetEqual(
      set(target_dict['failures'][1]['dependencies']), expected_deps2
    )
    self.assertListEqual(warning_collector.get(), [])

  def testGetDetailedInfoNonCXXRules(self):
    warning_collector = ninja_wrapper.WarningCollector()
    ninja_parser = ninja_wrapper.NinjaBuildOutputStreamingParser(
      warning_collector
    )
    for line in _NINJA_STDOUT_NON_CXX_RULE.splitlines():
      ninja_parser.parse(line)
    failed_target_list = ninja_parser.failed_target_list
    target_dict = ninja_wrapper.get_detailed_info(
      '', '', failed_target_list, warning_collector
    )

    expected_deps1 = set([])
    expected_deps2 = set([])
    expected_deps3 = set([])
    expected_deps4 = set([])
    self.assertSetEqual(
      set(target_dict['failures'][0]['dependencies']), expected_deps1
    )
    self.assertSetEqual(
      set(target_dict['failures'][1]['dependencies']), expected_deps2
    )
    self.assertSetEqual(
      set(target_dict['failures'][2]['dependencies']), expected_deps3
    )
    self.assertSetEqual(
      set(target_dict['failures'][3]['dependencies']), expected_deps4
    )
    self.assertListEqual(warning_collector.get(), [])

  @unittest.mock.patch(
    'ninja_wrapper.run_ninja_tool',
    side_effect=[_DEPS_NOT_FOUND_RETURN, _GRAPH_RETURN],
  )
  def testGetDetailedInfoMixedRules(self, _):
    warning_collector = ninja_wrapper.WarningCollector()
    ninja_parser = ninja_wrapper.NinjaBuildOutputStreamingParser(
      warning_collector
    )
    for line in _NINJA_STDOUT_MIXED_RULE.splitlines():
      ninja_parser.parse(line)
    failed_target_list = ninja_parser.failed_target_list
    target_dict = ninja_wrapper.get_detailed_info(
      '', '', failed_target_list, warning_collector
    )
    failure_outputs = ninja_parser.failure_outputs

    expected_deps1 = set([])
    expected_deps2 = set(['../../base/b.cc', '../../base/b.h', '../../b.cc'])
    expected_deps3 = set([])

    expected_failure_outputs = (
      # Drop last line containing 'ninja: build stopped: ....'
      # and concat with newline
      '\n'.join(_NINJA_STDOUT_MIXED_RULE.splitlines()[:-1]) + '\n'
    )
    self.assertSetEqual(
      set(target_dict['failures'][0]['dependencies']), expected_deps1
    )
    self.assertSetEqual(
      set(target_dict['failures'][1]['dependencies']), expected_deps2
    )
    self.assertSetEqual(
      set(target_dict['failures'][2]['dependencies']), expected_deps3
    )
    self.assertListEqual(warning_collector.get(), [])
    self.assertEqual(failure_outputs, expected_failure_outputs)

  def testParseArgs(self):
    expected_file_name = 'file.json'
    expected_ninja_cmd = [
      'ninja',
      '-C',
      'build/path',
      'target1',
      'target2',
      '-o',
      'output',
    ]
    args = ['-o', expected_file_name]
    args.append('--')
    args.extend(expected_ninja_cmd)
    options = ninja_wrapper.parse_args(args)
    self.assertListEqual(expected_ninja_cmd, options.ninja_cmd)
    self.assertEqual(expected_file_name, options.ninja_info_output)

  def testParseArgsFullName(self):
    expected_file_name = 'file.json'
    expected_ninja_cmd = [
      'ninja',
      '-C',
      'build/path',
      'target1',
      'target2',
      '-o',
      'output',
    ]
    args = ['--ninja_info_output', expected_file_name]
    args.append('--')
    args.extend(expected_ninja_cmd)
    options = ninja_wrapper.parse_args(args)
    self.assertListEqual(expected_ninja_cmd, options.ninja_cmd)
    self.assertEqual(expected_file_name, options.ninja_info_output)

  def testParseArgsWithoutFile(self):
    expected_ninja_cmd = ['ninja', '-C', 'build/path', 'target1', 'target2']
    args = []
    args.append('--')
    args.extend(expected_ninja_cmd)
    options = ninja_wrapper.parse_args(args)
    self.assertListEqual(expected_ninja_cmd, options.ninja_cmd)
    self.assertIsNone(options.ninja_info_output)

  @unittest.mock.patch('ninja_wrapper.print')
  @unittest.mock.patch('ninja_wrapper.subprocess.Popen')
  def testMainWithTimeoutOk(self, mock_Popen, mock_print):
    """Test main() with a timeout that is not raised"""

    mock_popen_instance = unittest.mock.MagicMock()
    mock_popen_instance.stdout.readline.side_effect = [b'a', b'b', b'c', b'']
    mock_Popen.return_value = mock_popen_instance

    output_path = os.path.join(tempfile.mkdtemp(), 'output')
    ninja_wrapper.main(
      ['-o', output_path, '-t', '1', '--']
      + ['ninja', 'build/path', 'target1', 'target2']
    )

    self.assertEqual(mock_print.call_count, 3)
    mock_popen_instance.stdout.close.assert_called()

  @unittest.mock.patch('ninja_wrapper.print')
  @unittest.mock.patch('ninja_wrapper.subprocess.Popen')
  @unittest.mock.patch('ninja_wrapper.subprocess.call')
  def testMainWithTimeoutNotOk(self, mock_call, mock_Popen, mock_print):
    """Test main() with a timeout that is raised"""

    def faulty_readline_sequence():
      yield b'a'
      yield b'b'
      time.sleep(1.0)
      raise Exception(
        'This should not be reached, a timeout should have occurred'
      )

    # Makes faulty_readline behave like a regular function not like a generator
    faulty_readline = faulty_readline_sequence().__next__

    mock_popen_instance = unittest.mock.MagicMock()
    mock_popen_instance.stdout.readline = faulty_readline
    mock_Popen.return_value = mock_popen_instance

    retval = ninja_wrapper.main(
      ['-t', '.1', '--', 'ninja', 'build/path', 'target1', 'target2']
    )

    # Only two of these are from readline; one should be the io_timeout print,
    # and two for the header and footer prints in the process-list debugging.
    self.assertEqual(mock_print.call_count, 5)
    self.assertEqual(mock_call.call_count, 1)
    self.assertEqual(retval, 124)
    mock_popen_instance.stdout.close.assert_called()

  @unittest.mock.patch('ninja_wrapper.print')
  @unittest.mock.patch('ninja_wrapper.subprocess.Popen')
  @unittest.mock.patch('ninja_wrapper.subprocess.call')
  def testMainBadEscaping(self, mock_call, mock_Popen, mock_print):
    """Test main() with an invalid escape sequence coming from GN."""

    mock_popen_instance = unittest.mock.MagicMock()
    # This should be encoded as '\xc3\x81l' but GN puts an extra '\' in front
    # of it (which is escaped as '\\'). Shell accepts this, and ninja_wrapper
    # needs to too.
    mock_popen_instance.stdout.readline.side_effect = [b'\\\xc3\\\x81l', b'']
    mock_Popen.return_value = mock_popen_instance

    ninja_wrapper.main(
      ['-t', '.1', '--', 'ninja', 'build/path', 'target1', 'target2']
    )

    self.assertEqual(mock_print.call_count, 1)
    mock_popen_instance.stdout.close.assert_called()


if __name__ == '__main__':
  unittest.main()
