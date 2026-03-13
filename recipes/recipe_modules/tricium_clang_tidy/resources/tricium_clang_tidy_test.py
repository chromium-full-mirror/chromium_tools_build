#!/usr/bin/env vpython3
# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

# [VPYTHON:BEGIN]
# wheel: <
#    name: "infra/python/wheels/pyyaml-py3"
#    version: "version:5.3.1"
# >
# [VPYTHON:END]

from __future__ import annotations
from __future__ import print_function

import collections
import dataclasses
import io
import json
import logging
import os
import sys
import unittest

import tricium_clang_tidy_script as tidy


def _to_stringio(contents):
  if sys.version_info[0] == 2 and isinstance(contents, str):
    contents = unicode(contents)
  return io.StringIO(contents)


def _parse_fixes_file_text(line_offsets,
                           contents_text,
                           tidy_invocation_dir='/tidy/dir'):
  return list(
      tidy._parse_tidy_fixes_file(
          read_line_offsets=lambda _file_path: line_offsets,
          stream=_to_stringio(contents_text),
          tidy_invocation_dir=tidy_invocation_dir))


def _convert_tidy_diags_to_yaml(tidy_diags):
  """Converts the given _TidyDiagnostics to their YAML form.

    Note that the file offset of each diagnostic == its index in tidy_diags.
  """
  diagnostics = [{
      'DiagnosticName': diag.diag_name,
      'DiagnosticMessage': {
          'Message': diag.message,
          'FilePath': diag.file_path,
          'FileOffset': i,
      },
  } for i, diag in enumerate(tidy_diags)]

  # YAML doesn't have a dumps() on my local machine; JSON's easiest.
  return json.dumps({
      'Diagnostics': diagnostics,
  })


def _build_tidy_diagnostic(file_path,
                           line_number,
                           diag_name,
                           message,
                           replacements=(),
                           expansion_locs=(),
                           notes=()):
  """Builds a _TidyDiagnostic with a few default values for convenience."""
  return tidy._TidyDiagnostic(
      file_path=file_path,
      line_number=line_number,
      diag_name=diag_name,
      message=message,
      replacements=replacements,
      expansion_locs=expansion_locs,
      notes=notes,
  )


class _SilencingFilter:

  def __init__(self):
    pass

  def filter(self, _record):
    return 0


class Tests(unittest.TestCase):

  def _silence_logs(self):
    root = logging.getLogger()
    filt = _SilencingFilter()
    root.addFilter(filt)
    self.addCleanup(root.removeFilter, filt)

  def test_parse_compile_commands_raises_on_no_object(self):
    input_json = json.dumps([
        {
            'command': '/path/to/not an object file.o',
            'directory': '/dir/ect/ory',
            'file': 'foo.cc',
        },
    ])

    with self.assertRaises(ValueError) as ex:
      list(
          tidy._parse_compile_commands(
              _to_stringio(input_json), clang_cl=False))

    self.assertIn('lacks an output file', str(ex.exception))

  def test_parse_compile_commands_works_for_win(self):

    def compile_command(command):
      return {
          'command': command,
          'directory': '/dir/ect/ory',
          'file': 'foo.cc',
      }

    input_json = json.dumps([
        compile_command('/path/to/clang-cl.exe foo /Fo foo.o'),
        compile_command(
            '/path/to/rewrapper.exe /path/to/clang-cl.exe foo /Fo foo-2.o'),
    ])

    results = list(
        tidy._parse_compile_commands(_to_stringio(input_json), clang_cl=True))
    self.assertEqual(results, [
        tidy._CompileCommand(
            target_name='foo.o',
            file_abspath='/dir/ect/ory/foo.cc',
            file='foo.cc',
            directory='/dir/ect/ory',
            command='/path/to/clang-cl.exe foo /Fo foo.o',
            is_clang_cl_command=True,
        ),
        tidy._CompileCommand(
            target_name='foo-2.o',
            file_abspath='/dir/ect/ory/foo.cc',
            file='foo.cc',
            directory='/dir/ect/ory',
            command='/path/to/rewrapper.exe /path/to/clang-cl.exe foo /Fo foo-2.o',
            is_clang_cl_command=True,
        ),
    ])

  def test_broken_yaml_is_parse_error(self):
    no_offsets = tidy._LineOffsetMap.for_text('')
    with self.assertRaises(tidy._ParseError) as err:
      _parse_fixes_file_text(no_offsets, '{')
    self.assertIn('Broken yaml', str(err.exception))

    with self.assertRaises(tidy._ParseError) as err:
      # Missing keys
      _parse_fixes_file_text(no_offsets, '{"Diagnostics": [{}]}')
    self.assertIn('missing key', str(err.exception))

  def test_successful_tidy_file_parses(self):
    no_offsets = tidy._LineOffsetMap.for_text('')
    self.assertEqual(_parse_fixes_file_text(no_offsets, ''), [])

    tidy_invocation_dir = '/tidy'
    input_file = tidy._LineOffsetMap.for_text('a\nb')
    tidy_diags = [
        _build_tidy_diagnostic(
            file_path='/foo1',
            line_number=1,
            diag_name='-Wfoo1',
            message='foo1'),
        _build_tidy_diagnostic(
            file_path='/foo2',
            line_number=1,
            diag_name='-Wfoo2',
            message='foo2'),
        _build_tidy_diagnostic(
            file_path='foo3', line_number=2, diag_name='-Wfoo3',
            message='foo3'),
    ]

    diag_yaml = _convert_tidy_diags_to_yaml(tidy_diags)
    tidy_diags[-1] = dataclasses.replace(tidy_diags[-1], file_path='/tidy/foo3')
    # YAML doesn't have a dumps() on my local machine; JSON's easiest.
    self.assertEqual(
        _parse_fixes_file_text(input_file, diag_yaml, tidy_invocation_dir),
        tidy_diags)

  def test_expansion_locs_are_parsed_from_notes(self):
    yaml = '\n'.join([
        'MainSourceFile:  "/tmp/x.c"',
        'Diagnostics:',
        ' - DiagnosticName:  google-explicit-constructor',
        '   DiagnosticMessage:',
        '     Message:         foo',
        '     FilePath:        "/tmp/x.c"',
        '     FileOffset:      3',
        '     Replacements:    []',
        '   Notes:',
        '     - Message:         "expanded from macro \'\'a\'\'"',
        '       FilePath:        "/tmp/x.h"',
        '       FileOffset:      2',
        '     - Message:         "expanded from macro \'\'b\'\'"',
        '       FilePath:        "/tmp/x.h"',
        '       FileOffset:      1',
        '',
    ])

    def read_line_offsets(file_path):
      if file_path == '/tmp/x.c':
        return tidy._LineOffsetMap([0, 1])
      if file_path == '/tmp/x.h':
        return tidy._LineOffsetMap([1])
      # `return`'ing this to please pylint's R1710
      # (inconsistent-return-statements).
      return self.fail('Unknown parsed file path: %r' % file_path)

    diags = list(
        tidy._parse_tidy_fixes_file(
            read_line_offsets=read_line_offsets,
            stream=_to_stringio(yaml),
            tidy_invocation_dir='/tidy'))

    self.assertEqual(diags, [
        _build_tidy_diagnostic(
            file_path='/tmp/x.c',
            line_number=3,
            diag_name='google-explicit-constructor',
            message='foo',
            expansion_locs=(
                tidy._ExpandedFrom(file_path='/tmp/x.h', line_number=2),
                tidy._ExpandedFrom(file_path='/tmp/x.h', line_number=1),
            ),
        )
    ])

  def test_fix_parsing_handles_multiple_files_gracefully(self):
    tidy_diags = [
        _build_tidy_diagnostic(
            file_path='/foo1',
            line_number=1,
            diag_name='-Wfoo1',
            message='foo1'),
        _build_tidy_diagnostic(
            file_path='/foo1',
            line_number=1,
            diag_name='-Wfoo1',
            message='foo1'),
        _build_tidy_diagnostic(
            file_path='/foo1',
            line_number=2,
            diag_name='-Wfoo1',
            message='foo1'),
        _build_tidy_diagnostic(
            file_path='/foo2',
            line_number=1,
            diag_name='-Wfoo2',
            message='foo2'),
    ]
    diag_yaml = _convert_tidy_diags_to_yaml(tidy_diags)

    def read_file_offsets(file_path):
      if file_path == '/foo1':
        return tidy._LineOffsetMap.for_text('_\n')
      if file_path == '/foo2':
        return tidy._LineOffsetMap.for_text('')
      # `return`'ing this to please pylint's R1710
      # (inconsistent-return-statements).
      return self.fail('Unknown file path %s' % file_path)

    fixes = list(
        tidy._parse_tidy_fixes_file(
            read_file_offsets,
            _to_stringio(diag_yaml),
            tidy_invocation_dir='/tidy'))
    self.assertEqual(fixes, tidy_diags)

  def test_tidy_notes_are_parsed_from_notes(self):
    yaml = '\n'.join([
        'MainSourceFile:  "/tmp/x.c"',
        'Diagnostics:',
        ' - DiagnosticName:  google-explicit-constructor',
        '   DiagnosticMessage:',
        '     Message:         foo',
        '     FilePath:        "/tmp/x.c"',
        '     FileOffset:      3',
        '     Replacements:    []',
        '   Notes:',
        '     - Message:         "expanded from macro \'\'a\'\'"',
        '       FilePath:        "/tmp/x.h"',
        '       FileOffset:      3',
        '     - Message:         "message 2"',
        '       FilePath:        "/tmp/x.h"',
        '       FileOffset:      2',
        '     - Message:         "message 1"',
        '       FilePath:        "/tmp/x.h"',
        '       FileOffset:      1',
        '     - Message:         "expanded from macro \'\'a\'\'"',
        '       FilePath:        "/tmp/x.h"',
        '       FileOffset:      2',
        '',
    ])

    def read_line_offsets(file_path):
      if file_path == '/tmp/x.c':
        return tidy._LineOffsetMap([0, 1])
      if file_path == '/tmp/x.h':
        return tidy._LineOffsetMap([1, 2])
      # `return`'ing this to please pylint's R1710
      # (inconsistent-return-statements).
      return self.fail('Unknown parsed file path: %r' % file_path)

    diags = list(
        tidy._parse_tidy_fixes_file(
            read_line_offsets=read_line_offsets,
            stream=_to_stringio(yaml),
            tidy_invocation_dir='/tidy'))

    self.assertEqual(diags, [
        _build_tidy_diagnostic(
            file_path='/tmp/x.c',
            line_number=3,
            diag_name='google-explicit-constructor',
            message='foo',
            expansion_locs=(tidy._ExpandedFrom(
                file_path='/tmp/x.h', line_number=3),),
            notes=(
                tidy._TidyNote(
                    file_path='/tmp/x.h',
                    line_number=2,
                    message='message 2',
                    expansion_locs=(),
                ),
                tidy._TidyNote(
                    file_path='/tmp/x.h',
                    line_number=1,
                    message='message 1',
                    expansion_locs=(tidy._ExpandedFrom(
                        file_path='/tmp/x.h', line_number=2),),
                ),
            ),
        )
    ])

  def test_fix_parsing_doesnt_read_the_same_offsets_twice(self):
    tidy_diags = [
        _build_tidy_diagnostic(
            file_path='/foo1',
            line_number=1,
            diag_name='-Wfoo1',
            message='foo1'),
        _build_tidy_diagnostic(
            file_path='/foo1',
            line_number=1,
            diag_name='-Wfoo1',
            message='foo1'),
        _build_tidy_diagnostic(
            file_path='/foo2',
            line_number=2,
            diag_name='-Wfoo2',
            message='foo2'),
    ]
    diag_yaml = _convert_tidy_diags_to_yaml(tidy_diags)
    retrievals = collections.defaultdict(int)

    def read_file_offsets(file_path):
      retrievals[file_path] += 1
      return tidy._LineOffsetMap.for_text('')

    list(
        tidy._parse_tidy_fixes_file(
            read_file_offsets,
            _to_stringio(diag_yaml),
            tidy_invocation_dir='/tidy'))
    self.assertEqual(retrievals, {'/foo1': 1, '/foo2': 1})

  def test_fix_parsing_handles_empty_file_paths_gracefully(self):
    tidy_diags = [
        _build_tidy_diagnostic(
            file_path='', line_number=1, diag_name='-Wfoo1', message='foo1'),
    ]
    self.assertEqual(
        _parse_fixes_file_text(
            line_offsets=None,
            contents_text=_convert_tidy_diags_to_yaml(tidy_diags),
            tidy_invocation_dir='/tidy'), tidy_diags)

  def test_generate_tidy_actions_only_generates_up_to_n_actions_per_src(self):
    self._silence_logs()

    def parse_ninja_deps(out_dir, base_path):
      return [
          ('foo.o', ['/foo.h', '/foo.cc']),
          ('bar.o', ['/foo.h', '/bar.cc']),
      ]

    # Each test case is a tuple of `(max_tidy_actions_per_file,
    # expected_tidy_actions)`.
    test_cases = [
        (1, {
            tidy._TidyAction(
                cc_file='/foo.cc',
                target='foo.o',
                in_dir='/in',
                flags='whee',
                flags_use_cl_driver_mode=False,
            ): ['/foo.h'],
        }),
        (2, {
            tidy._TidyAction(
                cc_file='/foo.cc',
                target='foo.o',
                in_dir='/in',
                flags='whee',
                flags_use_cl_driver_mode=False,
            ): ['/foo.h'],
            tidy._TidyAction(
                cc_file='/bar.cc',
                target='bar.o',
                in_dir='/in',
                flags='whee',
                flags_use_cl_driver_mode=False,
            ): ['/foo.h'],
        }),
    ]

    for max_actions, expected_actions in test_cases:
      actions, _ = tidy._generate_tidy_actions(
          out_dir='/out',
          base_path='/foo',
          only_src_files=['/foo.h'],
          run_ninja=lambda out_dir, base_path, object_targets: (),
          gn_desc=tidy._GnDesc(
              per_target_srcs={
                  '//rule': ['/foo.h', '/foo.cc', '/bar.cc'],
              },
              deps={},
          ),
          parse_ninja_deps=parse_ninja_deps,
          compile_commands=[
              tidy._CompileCommand(
                  target_name='foo.o',
                  file_abspath='/foo.cc',
                  file='foo.cc',
                  directory='/in',
                  command='whee',
                  is_clang_cl_command=False,
              ),
              tidy._CompileCommand(
                  target_name='bar.o',
                  file_abspath='/bar.cc',
                  file='bar.cc',
                  directory='/in',
                  command='whee',
                  is_clang_cl_command=False,
              ),
          ],
          max_tidy_actions_per_file=max_actions)

      self.assertEqual(actions, expected_actions)

  def test_generate_tidy_actions_works_with_cc_files(self):
    self._silence_logs()

    def run_ninja(out_dir, base_path, object_targets):
      self.assertEqual(out_dir, '/out')
      self.assertEqual(object_targets, ['bar.o', 'foo.o'])
      return ()

    compile_commands = [
        tidy._CompileCommand(
            target_name='foo.o',
            file_abspath='/foo.cc',
            file='foo.cc',
            directory='/in',
            command='whee',
            is_clang_cl_command=False,
        ),
        tidy._CompileCommand(
            target_name='bar.o',
            file_abspath='/bar.cc',
            file='bar.cc',
            directory='/in',
            command='whee',
            is_clang_cl_command=False,
        ),
    ]

    actions, failed = tidy._generate_tidy_actions(
        out_dir='/out',
        base_path='/foo',
        only_src_files=['/foo.cc', '/bar.cc'],
        run_ninja=run_ninja,
        gn_desc=tidy._GnDesc({}, {}),
        parse_ninja_deps=lambda out_dir, base_path: (),
        compile_commands=compile_commands)
    self.assertEqual(failed, [])
    self.assertEqual(
        actions, {
            tidy._TidyAction(
                cc_file='/foo.cc',
                target='foo.o',
                in_dir='/in',
                flags='whee',
                flags_use_cl_driver_mode=False,
            ): ['/foo.cc'],
            tidy._TidyAction(
                cc_file='/bar.cc',
                target='bar.o',
                in_dir='/in',
                flags='whee',
                flags_use_cl_driver_mode=False,
            ): ['/bar.cc'],
        })

  def test_generate_tidy_actions_includes_headers_in_output(self):
    self._silence_logs()

    def run_ninja(out_dir, base_path, object_targets):
      self.assertEqual(out_dir, '/out')
      self.assertEqual(object_targets, ['bar.o', 'foo.o'])
      return ()

    compile_commands = [
        tidy._CompileCommand(
            target_name='foo.o',
            file_abspath='/foo.cc',
            file='foo.cc',
            directory='/in',
            command='whee',
            is_clang_cl_command=False,
        ),
        tidy._CompileCommand(
            target_name='bar.o',
            file_abspath='/bar.cc',
            file='bar.cc',
            directory='/in',
            command='whee',
            is_clang_cl_command=False,
        ),
    ]

    def parse_ninja_deps(out_dir, base_path):
      return [
          ('foo.o', ['/foo.h', '/foo.cc']),
          ('bar.o', ['/bar.cc']),
      ]

    actions, failed = tidy._generate_tidy_actions(
        out_dir='/out',
        base_path='/foo',
        only_src_files=['/foo.cc', '/foo.h', '/bar.cc'],
        run_ninja=run_ninja,
        gn_desc=tidy._GnDesc(
            {
                '//my/awesome:target': ['/foo.cc', '/bar.cc', '/baz.cc'],
            }, {}),
        parse_ninja_deps=parse_ninja_deps,
        compile_commands=compile_commands)
    self.assertEqual(failed, [])
    self.assertEqual(
        actions, {
            tidy._TidyAction(
                cc_file='/foo.cc',
                target='foo.o',
                in_dir='/in',
                flags='whee',
                flags_use_cl_driver_mode=False,
            ): ['/foo.cc', '/foo.h'],
            tidy._TidyAction(
                cc_file='/bar.cc',
                target='bar.o',
                in_dir='/in',
                flags='whee',
                flags_use_cl_driver_mode=False,
            ): ['/bar.cc'],
        })

  def test_generate_tidy_actions_ignores_nonexistent_files(self):
    self._silence_logs()

    def run_ninja(out_dir, base_path, object_targets):
      self.assertEqual(object_targets, ['foo.cc.o'])
      return ()

    actions, failed = tidy._generate_tidy_actions(
        out_dir='/out',
        base_path='/foo',
        only_src_files=['/foo.cc', '/bar.cc'],
        run_ninja=run_ninja,
        gn_desc=tidy._GnDesc({}, {}),
        parse_ninja_deps=lambda out_dir, base_path: (),
        compile_commands=[
            tidy._CompileCommand(
                target_name='foo.cc.o',
                file_abspath='/foo.cc',
                file='foo.cc',
                directory='/in',
                command='whee',
                is_clang_cl_command=False,
            )
        ])
    self.assertEqual(failed, [])
    self.assertEqual(
        actions, {
            tidy._TidyAction(
                cc_file='/foo.cc',
                target='foo.cc.o',
                in_dir='/in',
                flags='whee',
                flags_use_cl_driver_mode=False,
            ): ['/foo.cc'],
        })

  def test_generate_tidy_actions_functions_with_no_src_file_filter(self):
    self._silence_logs()

    def run_ninja(out_dir, base_path, object_targets):
      self.assertEqual(out_dir, '/out')
      self.assertEqual(object_targets, ['foo.cc.o'])
      return ()

    actions, failed = tidy._generate_tidy_actions(
        out_dir='/out',
        base_path='/foo',
        only_src_files=None,
        run_ninja=run_ninja,
        parse_ninja_deps=lambda out_dir, base_path: (),
        gn_desc=tidy._GnDesc({}, {}),
        compile_commands=[
            tidy._CompileCommand(
                target_name='foo.cc.o',
                file_abspath='/foo.cc',
                file='foo.cc',
                directory='/in',
                command='whee',
                is_clang_cl_command=False,
            )
        ])

    self.assertEqual(failed, [])
    self.assertEqual(
        actions, {
            tidy._TidyAction(
                cc_file='/foo.cc',
                target='foo.cc.o',
                in_dir='/in',
                flags='whee',
                flags_use_cl_driver_mode=False,
            ): ['/foo.cc'],
        })

  def test_generate_tidy_actions_reports_failures(self):
    self._silence_logs()

    def run_ninja(out_dir, base_path, object_targets):
      return list(object_targets)

    actions, failed = tidy._generate_tidy_actions(
        out_dir='/out',
        base_path='/foo',
        only_src_files=None,
        run_ninja=run_ninja,
        parse_ninja_deps=lambda out_dir, base_path: (),
        gn_desc=tidy._GnDesc({}, {}),
        compile_commands={
            tidy._CompileCommand(
                target_name='foo.cc.o',
                file_abspath='/foo.cc',
                file='foo.cc',
                directory='/in',
                command='whee',
                is_clang_cl_command=True,
            )
        })
    expected_action = tidy._TidyAction(
        cc_file='/foo.cc',
        target='foo.cc.o',
        in_dir='/in',
        flags='whee',
        flags_use_cl_driver_mode=True)
    self.assertEqual(failed, [expected_action])
    self.assertEqual(actions, {expected_action: ['/foo.cc']})

  def test_run_all_tidy_actions_reports_everything(self):
    self._silence_logs()

    binary = object()
    tidy_checks = object()
    timeout_action = tidy._TidyAction(
        cc_file='timeout',
        target='timeout.o',
        in_dir='in/',
        flags='',
        flags_use_cl_driver_mode=False,
    )
    fail_action = tidy._TidyAction(
        cc_file='fail',
        target='fail.o',
        in_dir='in/',
        flags='',
        flags_use_cl_driver_mode=False,
    )
    good_action = tidy._TidyAction(
        cc_file='success',
        target='success.o',
        in_dir='in/',
        flags='',
        flags_use_cl_driver_mode=False,
    )
    actions = [timeout_action, fail_action, good_action]

    good_diag = _build_tidy_diagnostic(
        file_path='whee', line_number=1, diag_name='-Whee', message='whee')
    bad_diag = _build_tidy_diagnostic(
        file_path='oh_no', line_number=1, diag_name='-Whee', message='oh no')

    def runner(arg_binary, checks, action):
      self.assertIs(arg_binary, binary)
      self.assertIs(checks, tidy_checks)
      if action.cc_file == 'timeout':
        return None, '', []
      if action.cc_file == 'fail':
        return 1, '', [bad_diag]
      self.assertEqual(action.cc_file, 'success')
      return 0, '', [good_diag]

    failed_cc_files, timed_out_cc_files, findings = tidy._run_all_tidy_actions(
        actions,
        runner,
        tidy_jobs=len(actions),
        clang_tidy_binary=binary,
        clang_tidy_checks=tidy_checks,
        use_threads=True)

    self.assertEqual(failed_cc_files, {fail_action})
    self.assertEqual(timed_out_cc_files, {timeout_action})
    self.assertEqual(findings, {good_diag, bad_diag})

  def test_line_offset_map_handles_no_text_gracefully(self):
    no_text = tidy._LineOffsetMap.for_text('')
    self.assertEqual(no_text.get_line_number(0), 1)
    self.assertEqual(no_text.get_line_offset(0), 0)
    self.assertEqual(no_text.get_line_number(1), 1)
    self.assertEqual(no_text.get_line_offset(1), 1)

  def test_line_offset_map_is_next_line_after_eol_character(self):
    newline_start = tidy._LineOffsetMap.for_text('\n')
    self.assertEqual(newline_start.get_line_number(0), 1)
    self.assertEqual(newline_start.get_line_offset(0), 0)
    self.assertEqual(newline_start.get_line_number(1), 2)
    self.assertEqual(newline_start.get_line_offset(1), 0)
    self.assertEqual(newline_start.get_line_number(2), 2)
    self.assertEqual(newline_start.get_line_offset(2), 1)

  def test_line_offset_map_on_random_input(self):
    line_offset_pairs = [
        ('a', 1, 0),
        ('b', 1, 1),
        ('\n', 1, 2),
        ('c', 2, 0),
        ('\n', 2, 1),
        ('\n', 3, 0),
        ('d', 4, 0),
        ('', 4, 1),
        ('', 4, 2),
    ]
    text = tidy._LineOffsetMap.for_text(''.join(
        x for x, _, _ in line_offset_pairs))
    for offset, (_, line_number, line_offset) in enumerate(line_offset_pairs):
      self.assertEqual(text.get_line_number(offset), line_number)
      self.assertEqual(text.get_line_offset(offset), line_offset)

  def test_path_normalization_functions_at_all(self):
    self.assertEqual(tidy._normalize_path_to_base('/foo', base=None), '/foo')
    self.assertEqual(tidy._normalize_path_to_base('/foo', base='/'), 'foo')
    self.assertEqual(tidy._normalize_path_to_base('/foo', base='/foo'), '.')
    self.assertIsNone(tidy._normalize_path_to_base('/foo', base='/foo/bar'))
    self.assertIsNone(tidy._normalize_path_to_base('/bar', base='/foo'))
    self.assertIsNone(tidy._normalize_path_to_base('/bar', base='/ba'))

  def test_output_conversion_drops_paths_outside_of_the_base(self):
    self._silence_logs()

    # I don't _expect_ for paths like these to exist. That said, logging and
    # dropping sounds better than crashing, or producing nonsensical output.
    main_action = tidy._TidyAction(
        cc_file='/foo/bar.cc',
        target='bar.o',
        in_dir='in/',
        flags='',
        flags_use_cl_driver_mode=False,
    )

    result = tidy._convert_tidy_output_json_obj(
        base_path='/foo',
        tidy_actions={main_action: ['/not_in_base/bar.cc']},
        failed_actions=[main_action],
        failed_tidy_actions=[],
        timed_out_actions=[main_action],
        findings=[
            _build_tidy_diagnostic(
                file_path='', line_number=1, diag_name='bar', message='baz'),
            _build_tidy_diagnostic(
                file_path='/not_in_base/bar.cc',
                line_number=1,
                diag_name='bar',
                message='baz')
        ],
        only_src_files=None,
    )

    self.assertEqual(
        result, {
            'diagnostics': [],
            'failed_src_files': [],
            'failed_tidy_files': [],
            'timed_out_src_files': [],
        })

  def test_output_conversion_converts_non_diagnostic_paths_to_src_files(self):
    self._silence_logs()

    main_action = tidy._TidyAction(
        cc_file='/foo/cc_file.cc',
        target='bar.o',
        in_dir='in/',
        flags='',
        flags_use_cl_driver_mode=False,
    )
    secondary_action = tidy._TidyAction(
        cc_file='/foo/secondary_file.cc',
        target='baz.o',
        in_dir='in/',
        flags='',
        flags_use_cl_driver_mode=False,
    )
    tertiary_action = tidy._TidyAction(
        cc_file='/foo/tertiary_file.cc',
        target='qux.o',
        in_dir='in/',
        flags='',
        flags_use_cl_driver_mode=False,
    )
    result = tidy._convert_tidy_output_json_obj(
        base_path='/foo',
        tidy_actions={
            main_action: ['/foo/src_file.cc', '/foo/src_file.h'],
            secondary_action: ['/foo/secondary_file.cc', '/foo/src_file.h'],
            tertiary_action: ['/foo/tertiary_file.cc', '/foo/src_file.h'],
        },
        failed_actions=[main_action],
        failed_tidy_actions=[tertiary_action],
        timed_out_actions=[secondary_action],
        findings=[
            _build_tidy_diagnostic(
                file_path='/foo/not_src.h',
                line_number=1,
                diag_name='bar',
                message='baz'),
            _build_tidy_diagnostic(
                file_path='/foo/src_file.cc',
                line_number=1,
                diag_name='bar',
                message='baz'),
        ],
        only_src_files=None,
    )

    self.assertEqual(
        result, {
            'diagnostics': [
                dataclasses.asdict(
                    _build_tidy_diagnostic(
                        file_path='not_src.h',
                        line_number=1,
                        diag_name='bar',
                        message='baz')),
                dataclasses.asdict(
                    _build_tidy_diagnostic(
                        file_path='src_file.cc',
                        line_number=1,
                        diag_name='bar',
                        message='baz')),
            ],
            'failed_src_files': ['src_file.cc', 'src_file.h'],
            'failed_tidy_files': ['src_file.h', 'tertiary_file.cc'],
            'timed_out_src_files': ['secondary_file.cc', 'src_file.h'],
        })

  def test_output_conversion_doesnt_outputs_paths_only_once(self):
    self._silence_logs()

    main_action = tidy._TidyAction(
        cc_file='/foo/cc_file.cc',
        target='bar.o',
        in_dir='in/',
        flags='',
        flags_use_cl_driver_mode=False,
    )
    secondary_action = tidy._TidyAction(
        cc_file='/foo/secondary_file.cc',
        target='baz.o',
        in_dir='in/',
        flags='',
        flags_use_cl_driver_mode=False,
    )
    result = tidy._convert_tidy_output_json_obj(
        base_path='/foo',
        tidy_actions={
            main_action: ['/foo/src_file.cc'],
            secondary_action: ['/foo/bar/../src_file.cc'],
        },
        failed_actions=[main_action, secondary_action],
        failed_tidy_actions=[],
        timed_out_actions=[main_action, secondary_action],
        findings=[],
        only_src_files=None,
    )

    self.assertEqual(
        result, {
            'diagnostics': [],
            'failed_src_files': ['src_file.cc'],
            'failed_tidy_files': [],
            'timed_out_src_files': ['src_file.cc'],
        })

  def test_output_conversion_respects_src_file_filters(self):
    self._silence_logs()

    main_action = tidy._TidyAction(
        cc_file='/foo/src_file.cc',
        target='bar.o',
        in_dir='in/',
        flags='',
        flags_use_cl_driver_mode=False,
    )
    result = tidy._convert_tidy_output_json_obj(
        base_path='/foo',
        tidy_actions={
            main_action: ['/foo/src_file.cc', '/foo/src_file.h'],
        },
        failed_actions=[],
        failed_tidy_actions=[],
        timed_out_actions=[],
        findings=[
            _build_tidy_diagnostic(
                file_path='/foo/src_file.h',
                line_number=1,
                diag_name='bar',
                message='baz'),
            _build_tidy_diagnostic(
                file_path='/foo/src_file.cc',
                line_number=1,
                diag_name='bar',
                message='baz'),
        ],
        only_src_files=['/foo/src_file.cc'],
    )

    self.assertEqual(
        result, {
            'diagnostics': [
                dataclasses.asdict(
                    _build_tidy_diagnostic(
                        file_path='src_file.cc',
                        line_number=1,
                        diag_name='bar',
                        message='baz')),
            ],
            'failed_src_files': [],
            'failed_tidy_files': [],
            'timed_out_src_files': [],
        })

  def test_output_conversion_drops_notes_with_empty_paths(self):
    self._silence_logs()

    main_action = tidy._TidyAction(
        cc_file='/foo/src_file.cc',
        target='bar.o',
        in_dir='in/',
        flags='',
        flags_use_cl_driver_mode=False,
    )
    result = tidy._convert_tidy_output_json_obj(
        base_path='/foo',
        tidy_actions={
            main_action: ['/foo/src_file.cc', '/foo/src_file.h'],
        },
        failed_actions=[],
        failed_tidy_actions=[],
        timed_out_actions=[],
        findings=[
            _build_tidy_diagnostic(
                file_path='/foo/src_file.cc',
                line_number=1,
                diag_name='bar',
                message='baz',
                notes=(tidy._TidyNote(
                    file_path='',
                    line_number=0,
                    message='oh no',
                    expansion_locs=(),
                ),)),
        ],
        only_src_files=['/foo/src_file.cc'],
    )

    self.assertEqual(
        result, {
            'diagnostics': [
                dataclasses.asdict(
                    _build_tidy_diagnostic(
                        file_path='src_file.cc',
                        line_number=1,
                        diag_name='bar',
                        message='baz')),
            ],
            'failed_src_files': [],
            'failed_tidy_files': [],
            'timed_out_src_files': [],
        })

  def test_output_conversion_keeps_expansion_locs_with_empty_paths(self):
    self._silence_logs()

    main_action = tidy._TidyAction(
        cc_file='/foo/src_file.cc',
        target='bar.o',
        in_dir='in/',
        flags='',
        flags_use_cl_driver_mode=False,
    )
    result = tidy._convert_tidy_output_json_obj(
        base_path='/foo',
        tidy_actions={
            main_action: ['/foo/src_file.cc'],
        },
        failed_actions=[],
        failed_tidy_actions=[],
        timed_out_actions=[],
        findings=[
            _build_tidy_diagnostic(
                file_path='/foo/src_file.cc',
                line_number=1,
                diag_name='bar',
                message='baz',
                expansion_locs=(
                    tidy._ExpandedFrom(file_path='', line_number=1),
                    tidy._ExpandedFrom(
                        file_path='/foo/src_file.cc', line_number=2),
                ),
                notes=(tidy._TidyNote(
                    file_path='/foo/v8/src/common/globals.h',
                    line_number=786,
                    message='make conversion explicit to silence this warning',
                    expansion_locs=(tidy._ExpandedFrom(
                        file_path='', line_number=1),),
                ),),
            )
        ],
        only_src_files=None,
    )

    self.assertEqual(result['diagnostics'][0]['expansion_locs'], (
        dataclasses.asdict(tidy._ExpandedFrom(file_path='', line_number=1)),
        dataclasses.asdict(
            tidy._ExpandedFrom(file_path='src_file.cc', line_number=2)),
    ))
    self.assertEqual(result['diagnostics'][0]['notes'], (dataclasses.asdict(
        tidy._TidyNote(
            file_path='v8/src/common/globals.h',
            line_number=786,
            message='make conversion explicit to silence this warning',
            expansion_locs=(dataclasses.asdict(
                tidy._ExpandedFrom(file_path='', line_number=1)),),
        )),))

  def test_output_conversion_translates_expansion_locs(self):
    self._silence_logs()

    main_action = tidy._TidyAction(
        cc_file='/foo/src_file.cc',
        target='bar.o',
        in_dir='in/',
        flags='',
        flags_use_cl_driver_mode=False,
    )
    result = tidy._convert_tidy_output_json_obj(
        base_path='/foo',
        tidy_actions={
            main_action: ['/foo/src_file.cc', '/foo/src_file.h'],
        },
        failed_actions=[],
        failed_tidy_actions=[],
        timed_out_actions=[],
        findings=[
            _build_tidy_diagnostic(
                file_path='/foo/src_file.h',
                line_number=1,
                diag_name='bar',
                message='baz',
                expansion_locs=(
                    tidy._ExpandedFrom('/foo/src_file2.h', 1),
                    tidy._ExpandedFrom('/usr/include/src_file3.h', 2),
                ),
                notes=(
                    tidy._TidyNote(
                        file_path='/foo/src_file_dne.h',
                        line_number=1,
                        message='note',
                        expansion_locs=(
                            tidy._ExpandedFrom('/foo/src_file.h', 1),
                            tidy._ExpandedFrom('/bar/out_of_base.h', 3),
                        ),
                    ),
                    tidy._TidyNote(
                        file_path='/bar/src_file_dne.h',
                        line_number=1,
                        message='note outside of base',
                        expansion_locs=(),
                    ),
                ),
            ),
        ],
        only_src_files=None,
    )

    self.assertEqual(
        result, {
            'diagnostics': [
                dataclasses.asdict(
                    _build_tidy_diagnostic(
                        file_path='src_file.h',
                        line_number=1,
                        diag_name='bar',
                        message='baz',
                        expansion_locs=(
                            tidy._ExpandedFrom('src_file2.h', 1),
                            tidy._ExpandedFrom('/usr/include/src_file3.h', 2),
                        ),
                        notes=(tidy._TidyNote(
                            file_path='src_file_dne.h',
                            line_number=1,
                            message='note',
                            expansion_locs=(
                                tidy._ExpandedFrom('src_file.h', 1),
                                tidy._ExpandedFrom('/bar/out_of_base.h', 3),
                            ),
                        ),),
                    )),
            ],
            'failed_src_files': [],
            'failed_tidy_files': [],
            'timed_out_src_files': [],
        })

  def test_ninja_deps_parsing_filters_stale_entries(self):
    test_input = '\n'.join([
        'obj/foo.o: #deps 43, deps mtime 1579507293554707398 (STALE)',
        '    ../../tools/cfi/blacklist.txt',
        '    ../../base/foo_unittest.cc',
        'obj/bar.o: #deps 43, deps mtime 1579507293554707399 (VALID)',
        '    ../../tools/cfi/blacklist.txt',
        '    ../../base/bar_unittest.cc',
    ])

    output = list(
        tidy._parse_ninja_deps_output(_to_stringio(test_input), u'/in/dir'))
    self.assertEqual(output, [
        ('obj/bar.o', [
            '/in/dir/../../tools/cfi/blacklist.txt',
            '/in/dir/../../base/bar_unittest.cc'
        ]),
    ])

  def test_gn_desc_parsing_makes_only_file_paths_relative_to_root(self):
    gn_desc = {
        '//my_awesome:target': {
            'sources': ['//awesome.cpp'],
        },
    }

    results = tidy._parse_gn_desc_output(gn_desc, '/root')
    self.assertEqual(results.targets_containing('//awesome.cpp'), ())
    self.assertEqual(
        results.targets_containing('/root/awesome.cpp'),
        ['//my_awesome:target'])
    self.assertEqual(
        results.source_files_for_target('//my_awesome:target'),
        ['/root/awesome.cpp'])
    self.assertEqual(
        results.source_files_for_target('//my_awesome:target_v2'), ())

  def test_gn_desc_parsing_gracefully_handles_unknown_paths(self):
    gn_desc = {
        '//my_awesome:target': {
            'sources': ['//awesome.cpp'],
        },
    }

    results = tidy._parse_gn_desc_output(gn_desc, '/root')
    self.assertEqual(results.targets_containing('/doesnt/exist'), ())
    self.assertEqual(results.source_files_for_target('//doesnt/exist'), ())

  def test_buildable_src_files_returns_identity_if_file_is_buildable(self):
    cc_to_target_map = {
        '/foo.gen': ['foo.gen.o'],
        '/foo.cc': ['foo.cc.o'],
        '/foo.h': ['foo.h.o'],
    }

    no_desc = tidy._GnDesc({}, {})
    for target in cc_to_target_map:
      self.assertEqual(
          tidy._buildable_src_files_for(target, cc_to_target_map, no_desc),
          [target])

  def test_buildable_src_files_returns_nothing_if_nonbuildable_nonheader(self):
    cc_to_target_map = {
        '/foo.cc': ['foo.o'],
    }

    no_desc = tidy._GnDesc({}, {})
    self.assertEqual(
        tidy._buildable_src_files_for('/foo.inc', cc_to_target_map, no_desc),
        [])

  def test_buildable_src_files_returns_naive_rename_for_headers(self):
    cc_to_target_map = {
        '/foo.cc': ['foo.o'],
    }

    no_desc = tidy._GnDesc({}, {})
    self.assertEqual(
        tidy._buildable_src_files_for('/foo.h', cc_to_target_map, no_desc),
        ['/foo.cc'])

  def test_perform_build_builds_all_targets(self):
    self._silence_logs()

    cc_to_target_map = {
        '/foo.cc': ['foo.o'],
        '/bar.cc': ['bar.o'],
    }

    def run_ninja(out_dir, base_path, object_targets):
      self.assertEqual(sorted(object_targets), ['bar.o', 'foo.o'])
      return ()

    tidy._perform_build(
        out_dir='/out',
        base_path='/foo',
        run_ninja=run_ninja,
        parse_ninja_deps=lambda out_dir, base_path: [('foo.o',
                                                      ['/foo.cc', '/foo.h'])],
        cc_to_target_map=cc_to_target_map,
        gn_desc=tidy._GnDesc(
            per_target_srcs={
                '//path/to/my:targ': ['/foo.cc', '/bar.cc', '/foo.h']
            },
            deps={},
        ),
        potential_src_cc_file_deps={
            '/foo.h': ['/foo.cc', '/bar.cc'],
        })

  def test_perform_build_falls_back_to_deps_build_if_deps_werent_found(self):
    self._silence_logs()

    cc_to_target_map = {
        '/foo.cc': ['foo.o'],
        '/bar.cc': ['bar.o'],
        '/rdep.cc': ['rdep.o'],
    }

    built_objects = []

    def run_ninja(out_dir, base_path, object_targets):
      built_objects.append(object_targets)
      return ()

    tidy._perform_build(
        out_dir='/out',
        base_path='/foo',
        run_ninja=run_ninja,
        parse_ninja_deps=lambda out_dir, base_path: [('foo.o', ['/foo.cc'])],
        cc_to_target_map=cc_to_target_map,
        gn_desc=tidy._GnDesc(
            per_target_srcs={
                '//path/to/my:targ': ['/foo.cc', '/foo.h'],
                '//path/to/my:rdep': ['/rdep.cc', '/rdep.h'],
            },
            deps={
                '//path/to/my:rdep': ['//path/to/my:targ'],
            }),
        potential_src_cc_file_deps={
            '/foo.h': ['/foo.cc'],
        })

    self.assertEqual(built_objects, [['foo.o'], ['rdep.o']])

  def test_perform_build_reported_dependency_information_is_correct(self):
    self._silence_logs()

    cc_to_target_map = {
        '/foo.cc': ['foo.o'],
        '/bar.cc': ['bar.o'],
        '/baz.cc': ['baz.o'],
    }

    src_file_to_target_map, _ = tidy._perform_build(
        out_dir='/out',
        base_path='/foo',
        run_ninja=lambda out_dir, base_path, object_targets: (),
        parse_ninja_deps=lambda out_dir, base_path: [
            ('foo.o', ['/foo.cc', '/foo.h']),
            ('bar.o', ['/bar.cc', '/foo.h']),
            ('baz.o', ['/baz.cc']),
        ],
        cc_to_target_map=cc_to_target_map,
        gn_desc=tidy._GnDesc(
            per_target_srcs={
                '//path/to/my:targ': [
                    '/foo.cc', '/foo.h', '/bar.cc', '/baz.cc'
                ],
            },
            deps={},
        ),
        potential_src_cc_file_deps={
            '/foo.h': ['/foo.cc', '/bar.cc', '/baz.cc'],
        })

    self.assertEqual(src_file_to_target_map, {
        '/foo.h': {'foo.o', 'bar.o'},
    })

  def test_perform_build_traverses_transitive_rdeps_for_ones_with_sources(self):
    self._silence_logs()

    cc_to_target_map = {
        '/foo.cc': ['foo.o'],
        '/bar.cc': ['bar.o'],
        '/rdep.cc': ['rdep.o'],
        '/rdep2.cc': ['rdep2.o'],
        '/rdep3.cc': ['rdep3.o'],
        '/rdep4.cc': ['rdep4.o'],
    }

    built_objects = []

    def run_ninja(out_dir, base_path, object_targets):
      built_objects.append(object_targets)
      return ()

    tidy._perform_build(
        out_dir='/out',
        base_path='/foo',
        run_ninja=run_ninja,
        parse_ninja_deps=lambda out_dir, base_path: [('foo.o', ['/foo.cc'])],
        cc_to_target_map=cc_to_target_map,
        gn_desc=tidy._GnDesc(
            per_target_srcs={
                '//path/to/my:targ': ['/foo.cc', '/foo.h'],
                '//path/to/my:rdep': ['/rdep.cc', '/rdep.h'],
                '//path/to/my:srcless_rdep2.1': ['/rdep2.cc', '/rdep2.h'],
                '//path/to/my:srcless_rdep2.2.1': ['/rdep3.cc', '/rdep3.h'],
                '//path/to/my:srcless_rdep2.3': ['/rdep4.h'],
                '//path/to/my:srcless_rdep2.3.1': ['/rdep4.cc'],
            },
            deps={
                '//path/to/my:rdep': ['//path/to/my:targ'],
                '//path/to/my:srcless_rdep': ['//path/to/my:targ'],
                '//path/to/my:srcless_rdep2': ['//path/to/my:srcless_rdep'],
                # Branch out to an rdep with no sources.
                '//path/to/my:srcless_rdep2.1': ['//path/to/my:srcless_rdep2'],
                # ...And to an rdep which has sources in the singular rdep of
                # it.
                '//path/to/my:srcless_rdep2.2': ['//path/to/my:srcless_rdep2'],
                '//path/to/my:srcless_rdep2.2.1': [
                    '//path/to/my:srcless_rdep2.2'
                ],
                # ...And to an rdep that has header-only sources associated.
                '//path/to/my:srcless_rdep2.3': ['//path/to/my:srcless_rdep2'],
                '//path/to/my:srcless_rdep2.3.1': [
                    '//path/to/my:srcless_rdep2.3'
                ],
            }),
        potential_src_cc_file_deps={
            '/foo.h': ['/foo.cc'],
        })

    self.assertEqual(built_objects,
                     [['foo.o'], ['rdep.o', 'rdep2.o', 'rdep3.o', 'rdep4.o']])

  def test_generate_tidy_actions_copes_with_unknown_objects(self):
    self._silence_logs()

    def parse_ninja_deps(out_dir, base_path):
      # foo_pnacl.o not being present in `compile_commands` broke us before;
      # crbug.com/1067271
      return [
          ('foo_pnacl.o', ['/foo.h', '/foo.cc']),
      ]

    actions, _ = tidy._generate_tidy_actions(
        out_dir='/out',
        base_path='/foo',
        only_src_files=['/foo.h'],
        run_ninja=lambda out_dir, base_path, object_targets: (),
        gn_desc=tidy._GnDesc(
            {
                '//rule': ['/foo.h', '/foo.cc'],
            },
            deps={},
        ),
        parse_ninja_deps=parse_ninja_deps,
        compile_commands=[
            tidy._CompileCommand(
                target_name='foo.o',
                file_abspath='/foo.cc',
                file='foo.cc',
                directory='/in',
                command='whee',
                is_clang_cl_command=False,
            ),
        ])
    self.assertEqual(actions, {})

  def test_rdep_determination_respects_limits(self):
    self._silence_logs()

    gn_desc = tidy._GnDesc(
        per_target_srcs={
            '//foo': ['foo.h'],
            '//foo_user': ['foo_user.cc'],
        },
        deps={
            '//foo_user': ['//foo'],
        },
    )

    cc_to_target_map = {
        'foo_user.cc': ['foo_user_1.o', 'foo_user_2.o'],
    }

    rdeps = tidy._determine_rdeps_to_build_for(
        missing_files=['foo.h'],
        gn_desc=gn_desc,
        cc_to_target_map=cc_to_target_map,
        action_limit=1,
    )
    self.assertEqual(rdeps, [])

    rdeps = tidy._determine_rdeps_to_build_for(
        missing_files=['foo.h'],
        gn_desc=gn_desc,
        cc_to_target_map=cc_to_target_map,
        action_limit=2,
    )
    self.assertEqual(rdeps, ['//foo_user'])

  def test_rdep_determination_prioritizes_targets_refed_by_many_rdeps(self):
    self._silence_logs()

    gn_desc = tidy._GnDesc(
        per_target_srcs={
            '//foo': ['foo.h'],
            '//foo_user': ['foo_user.cc'],
            '//bar': ['bar.h'],
            '//bar_user': ['bar_user.cc'],
            '//foo_and_bar_user': ['foo_and_bar_user.cc'],
        },
        deps={
            '//foo_user': ['//foo'],
            '//bar_user': ['//bar'],
            '//foo_and_bar_user': ['//bar', '//foo'],
        },
    )

    cc_to_target_map = {
        'foo_user.cc': ['foo_user_1.o', 'foo_user_2.o'],
        'bar_user.cc': ['bar_user_1.o', 'bar_user_2.o'],
        'foo_and_bar_user.cc': ['foo_and_bar_user_1.o', 'foo_and_bar_user_2.o'],
    }

    rdeps = tidy._determine_rdeps_to_build_for(
        missing_files=['bar.h', 'foo.h'],
        gn_desc=gn_desc,
        cc_to_target_map=cc_to_target_map,
        action_limit=2,
    )
    self.assertEqual(rdeps, ['//foo_and_bar_user'])

  def test_rdep_determination_prioritizes_targets_with_fewer_objects(self):
    self._silence_logs()

    gn_desc = tidy._GnDesc(
        per_target_srcs={
            '//foo': ['foo.h'],
            '//large_foo_user': ['large_foo_user.cc'],
            '//small_foo_user': ['small_foo_user.cc'],
        },
        deps={
            '//large_foo_user': ['//foo'],
            '//small_foo_user': ['//foo'],
        },
    )

    cc_to_target_map = {
        'large_foo_user.cc': ['large1.o', 'large2.o', 'large3.o'],
        'small_foo_user.cc': ['small1.o', 'small2.o'],
    }

    rdeps = tidy._determine_rdeps_to_build_for(
        missing_files=['foo.h'],
        gn_desc=gn_desc,
        cc_to_target_map=cc_to_target_map,
        action_limit=3,
    )
    self.assertEqual(rdeps, ['//small_foo_user'])

  def test_rdep_determination_ignores_empty_rdeps(self):
    self._silence_logs()

    gn_desc = tidy._GnDesc(
        per_target_srcs={
            '//foo': ['foo.h'],
            '//foo_user': ['foo_user.cc'],
            '//empty_foo_user1': ['empty_foo_user1.cc'],
            '//empty_foo_user2': ['empty_foo_user2.cc'],
        },
        deps={
            '//foo_user': ['//foo'],
            '//empty_foo_user1': ['//foo'],
            '//empty_foo_user2': ['//foo'],
        },
    )

    cc_to_target_map = {
        'foo_user.cc': ['foo_user_1.o', 'foo_user_2.o'],
        'empty_foo_user1.cc': (),
    }

    rdeps = tidy._determine_rdeps_to_build_for(
        missing_files=['foo.h'],
        gn_desc=gn_desc,
        cc_to_target_map=cc_to_target_map,
    )
    self.assertEqual(rdeps, ['//foo_user'])

  def test_rdep_determination_works_with_empty_cases(self):
    self._silence_logs()

    rdeps = tidy._determine_rdeps_to_build_for(
        missing_files=['foo.h'],
        gn_desc=tidy._GnDesc(
            per_target_srcs={},
            deps={},
        ),
        cc_to_target_map={},
    )
    self.assertEqual(rdeps, [])

    rdeps = tidy._determine_rdeps_to_build_for(
        missing_files=['foo.h'],
        gn_desc=tidy._GnDesc(
            per_target_srcs={'//foo': ['foo.h']},
            deps={},
        ),
        cc_to_target_map={},
    )
    self.assertEqual(rdeps, [])

    rdeps = tidy._determine_rdeps_to_build_for(
        missing_files=['foo.h'],
        gn_desc=tidy._GnDesc(
            per_target_srcs={'//foo': ['foo.h']},
            deps={
                '//foo_user': ['foo_user.cc'],
            },
        ),
        cc_to_target_map={},
    )
    self.assertEqual(rdeps, [])

  def test_trim_rewrapper_command(self):
    commands = [
        ['/path/to/clang++', 'foo', '-o', 'foo.o'],
        [
            '/path/to/rewrapper', '-cfg=../../path/to/file.cfg',
            '-other_rewrapper_flag', '-exec_root=/a/b/c', '/path/to/clang++',
            'foo', '-o', 'foo.o'
        ],
    ]

    trimmed_commands = [
        tidy._trim_rewrapper_command(command) for command in commands
    ]

    self.assertEqual(trimmed_commands, [
        ['/path/to/clang++', 'foo', '-o', 'foo.o'],
        ['/path/to/clang++', 'foo', '-o', 'foo.o'],
    ])


if __name__ == '__main__':
  # Init logging here so users can remove `self._silence_logs()` from any of
  # the tests to get more verbose output.
  tidy._init_logging(debug=True)
  unittest.main()
