# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import json

from recipe_engine import post_process
from RECIPE_MODULES.build.tricium_clang_tidy import _clang_tidy_path

DEPS = [
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/step',
    'recipe_engine/tricium',
    'tricium_clang_tidy',
    'reclient',
]

def RunSteps(api):
  cache_dir = api.path.cache_dir
  source_dir = cache_dir / 'builder' / 'src'
  with api.context(cwd=cache_dir):
    # file_paths should be kept in sync with the paths used in test below.
    api.tricium_clang_tidy.lint_source_files(
        source_dir,
        output_dir=cache_dir / 'out',
        file_paths=[cache_dir.joinpath('src', 'path/to/some/cc/file.cpp')],
        is_windows=api.properties['is_windows'])


def _get_tricium_comments(steps):
  write_results = steps['write results']
  tricium_json = write_results.output_properties['tricium']
  return json.loads(tricium_json).get('comments')


def _tricium_has_no_messages(check, steps):
  comments = _get_tricium_comments(steps)
  check(not comments)


def _tricium_has_message(check, steps, message):
  comments = _get_tricium_comments(steps)
  check(comments)
  if comments:
    check(message in [x['message'] for x in comments])


def _tricium_has_replacements(check, steps, *expected_replacements):
  replacement_messages = set()
  for comment in _get_tricium_comments(steps):
    for suggestion in comment.get('suggestions', ()):
      for replacement in suggestion['replacements']:
        replacement_messages.add(replacement['replacement'])

  check(set(expected_replacements) == replacement_messages)


def _tricium_outputs_json(check, steps, json_obj):
  comments = _get_tricium_comments(steps)
  check(comments == json_obj)


def GenTests(api):

  def with_patch(affected_files,
                 auto_exist_files=True,
                 clang_tidy_exists=True,
                 is_windows=False):
    test_data = api.properties(is_windows=is_windows)

    existing_files = []
    if auto_exist_files:
      existing_files += [
          api.path.cache_dir.joinpath('src', x) for x in affected_files
      ]

    if clang_tidy_exists:
      existing_files.append(api.path.cache_dir.joinpath(*_clang_tidy_path))

    if existing_files:
      test_data += api.path.exists(*existing_files)
    return test_data

  yield api.test(
      'no_files',
      with_patch(affected_files=[]),
      api.reclient.properties(),
      api.post_process(post_process.DoesNotRun, 'clang-tidy'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'no_analysis_non_cpp',
      with_patch(affected_files=['some/cc/file.txt']),
      api.reclient.properties(),
      api.post_process(post_process.DoesNotRun, 'clang-tidy'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'removed_file',
      with_patch(
          affected_files=['path/to/some/cc/file.cpp'], auto_exist_files=False),
      api.reclient.properties(),
      api.post_process(post_process.DoesNotRun, 'clang-tidy'),
      api.post_process(_tricium_has_no_messages),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'analyze_cpp_timed_out_files',
      with_patch(affected_files=['path/to/some/cc/file.cpp']),
      api.reclient.properties(),
      api.step_data('clang-tidy.generate-warnings.read tidy output',
                    api.file.read_json({'timed_out_src_files': ['oh/no.cpp']})),
      api.post_process(post_process.StepWarning,
                       'clang-tidy.generate-warnings'),
      api.post_process(
          _tricium_has_message, 'warning: clang-tidy timed out on this '
          'file; issuing diagnostics is impossible.'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'analyze_cpp_failed_files',
      with_patch(affected_files=['path/to/some/cc/file.cpp']),
      api.reclient.properties(),
      api.step_data(
          'clang-tidy.generate-warnings.read tidy output',
          api.file.read_json({'failed_src_files': ['path/to/some/cc/file.cpp']
                             })),
      api.post_process(post_process.StepWarning,
                       'clang-tidy.generate-warnings'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'analyze_cpp_failed_tidy_files',
      with_patch(affected_files=['path/to/some/cc/file.cpp']),
      api.reclient.properties(),
      api.step_data(
          'clang-tidy.generate-warnings.read tidy output',
          api.file.read_json(
              {'failed_tidy_files': ['path/to/some/cc/file.cpp']})),
      api.post_process(post_process.StepWarning,
                       'clang-tidy.generate-warnings'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'analyze_cpp',
      with_patch(affected_files=['path/to/some/cc/file.cpp']),
      api.reclient.properties(),
      api.step_data(
          'clang-tidy.generate-warnings.read tidy output',
          api.file.read_json({
              'diagnostics': [
                  {
                      'file_path': 'path/to/some/cc/file.cpp',
                      'line_number': 2,
                      'diag_name': 'super-cool-diag',
                      'message': 'hello, world 1',
                      'replacements': [],
                      'expansion_locs': [],
                  },
                  {
                      'file_path': 'path/to/some/cc/file.cpp',
                      'line_number': 50,
                      'diag_name': 'moderately-cool-diag',
                      'message': 'hello, world',
                      'replacements': [],
                      'expansion_locs': [],
                  },
              ]
          })),
      api.post_process(post_process.StepSuccess,
                       'clang-tidy.generate-warnings'),
      api.post_process(
          _tricium_has_message, 'hello, world 1 (https://clang.llvm.org/'
          'extra/clang-tidy/checks/super/cool-diag.html)'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'analyze_cpp_windows',
      with_patch(affected_files=['path/to/some/cc/file.cpp'], is_windows=True),
      api.reclient.properties(),
      api.step_data(
          'clang-tidy.generate-warnings.read tidy output',
          api.file.read_json({
              'diagnostics': [{
                  'file_path': 'path\\to\\some\\cc/file.cpp',
                  'line_number': 2,
                  'diag_name': 'super-cool-diag',
                  'message': 'hello, world 1',
                  'replacements': [],
                  'expansion_locs': [],
              },]
          })),
      api.post_process(post_process.StepSuccess,
                       'clang-tidy.generate-warnings'),
      api.post_process(
          _tricium_has_message, 'hello, world 1 (https://clang.llvm.org/'
          'extra/clang-tidy/checks/super/cool-diag.html)'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'only_warnings_and_errors_are_silenced',
      with_patch(affected_files=['path/to/some/cc/file.cpp']),
      api.reclient.properties(),
      api.step_data(
          'clang-tidy.generate-warnings.read tidy output',
          api.file.read_json({
              'failed_src_files': ['path/to/some/cc/file.cpp'],
              'diagnostics': [
                  {
                      'file_path': 'path/to/some/cc/file.cpp',
                      'line_number': 2,
                      'diag_name': 'clang-diagnostic-warning',
                      'message': 'hello, world',
                      'replacements': [],
                      'expansion_locs': [],
                  },
                  {
                      'file_path': 'path/to/some/cc/file.cpp',
                      'line_number': 2,
                      'diag_name': 'clang-diagnostic-error',
                      'message': 'hello, world',
                      'replacements': [],
                      'expansion_locs': [],
                  },
              ],
          })),
      api.post_process(post_process.StepWarning,
                       'clang-tidy.generate-warnings'),
      api.post_process(_tricium_has_no_messages),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'append_complaint_on_failure',
      with_patch(affected_files=['path/to/some/cc/file.cpp']),
      api.reclient.properties(),
      api.step_data(
          'clang-tidy.generate-warnings.read tidy output',
          api.file.read_json({
              'failed_src_files': ['path/to/some/cc/file.cpp'],
              'diagnostics': [{
                  'file_path': 'path/to/some/cc/file.cpp',
                  'line_number': 2,
                  'diag_name': 'b',
                  'message': 'a',
                  'replacements': [],
                  'expansion_locs': [],
              },],
          })),
      api.post_process(post_process.StepWarning,
                       'clang-tidy.generate-warnings'),
      api.post_process(
          _tricium_has_message,
          'a (https://clang.llvm.org/extra/clang-tidy/checks/b.html)\n\n'
          '(Note: building this file or its dependencies failed; this '
          'diagnostic might be incorrect as a result.)'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'prefer_complaints_about_build_failures_over_tidy_ones',
      with_patch(affected_files=['path/to/some/cc/file.cpp']),
      api.reclient.properties(),
      api.step_data(
          'clang-tidy.generate-warnings.read tidy output',
          api.file.read_json({
              'failed_tidy_files': ['path/to/some/cc/file.cpp'],
              'failed_src_files': ['path/to/some/cc/file.cpp'],
              'diagnostics': [{
                  'file_path': 'path/to/some/cc/file.cpp',
                  'line_number': 2,
                  'diag_name': 'b',
                  'message': 'a',
                  'replacements': [],
                  'expansion_locs': [],
              },],
          })),
      api.post_process(post_process.StepWarning,
                       'clang-tidy.generate-warnings'),
      api.post_process(
          _tricium_has_message,
          'a (https://clang.llvm.org/extra/clang-tidy/checks/b.html)\n\n'
          '(Note: building this file or its dependencies failed; this '
          'diagnostic might be incorrect as a result.)'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'append_complaint_on_tidy_failure',
      with_patch(affected_files=['path/to/some/cc/file.cpp']),
      api.reclient.properties(),
      api.step_data(
          'clang-tidy.generate-warnings.read tidy output',
          api.file.read_json({
              'failed_tidy_files': ['path/to/some/cc/file.cpp'],
              'diagnostics': [{
                  'file_path': 'path/to/some/cc/file.cpp',
                  'line_number': 2,
                  'diag_name': 'b',
                  'message': 'a',
                  'replacements': [],
                  'expansion_locs': [],
              },],
          })),
      api.post_process(post_process.StepWarning,
                       'clang-tidy.generate-warnings'),
      api.post_process(
          _tricium_has_message,
          'a (https://clang.llvm.org/extra/clang-tidy/checks/b.html)\n\n'
          '(Note: running clang-tidy on this file failed; this '
          'diagnostic might be incorrect as a result.)'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'diagnostic_suggestions',
      with_patch(affected_files=['path/to/some/cc/file.cpp']),
      api.reclient.properties(),
      api.step_data(
          'clang-tidy.generate-warnings.read tidy output',
          api.file.read_json({
              'diagnostics': [{
                  'file_path': 'path/to/some/cc/file.cpp',
                  'line_number': 2,
                  'diag_name': 'tidy-is-angry',
                  'message': 'hello, world',
                  'replacements': [
                      {
                          'new_text': 'foo',
                          'start_line': 1,
                          'end_line': 2,
                          'start_char': 0,
                          'end_char': 1,
                      },
                      {
                          'new_text': 'bar',
                          'start_line': 3,
                          'end_line': 4,
                          'start_char': 5,
                          'end_char': 5,
                      },
                  ],
                  'expansion_locs': [],
              },]
          })),
      api.post_process(post_process.StepSuccess,
                       'clang-tidy.generate-warnings'),
      api.post_process(_tricium_has_replacements, 'foo', 'bar'),
      api.post_process(post_process.DropExpectation),
  )

  expansions_tests = [
      (1, 1, '.'),
      (2, 1, ', and 1 other place.'),
      (3, 1, ', and 2 other places.'),
      (4, 2, ', and 3 other places.'),
  ]

  for num_expansions, expansion_duplication, suffix in expansions_tests:
    diags = []
    for i in range(num_expansions):
      for _ in range(expansion_duplication):
        diags.append({
            'file_path':
                'path/to/some/cc/file%d.cpp' % i,
            'line_number':
                2,
            'diag_name':
                'tidy-is-angry',
            'message':
                'grrr',
            'replacements': [],
            'expansion_locs': [
                {
                    'file_path': 'path/to/some/cc/file%d.cpp' % i,
                    'line_number': 1,
                },
                {
                    'file_path': 'path/to/some/cc/file.h',
                    'line_number': 3,
                },
            ],
        })

    yield api.test(
        'expansion_%d' % num_expansions,
        with_patch(affected_files=['path/to/some/cc/file.cpp']),
        api.reclient.properties(),
        api.step_data('clang-tidy.generate-warnings.read tidy output',
                      api.file.read_json({'diagnostics': diags})),
        api.post_process(post_process.StepSuccess,
                         'clang-tidy.generate-warnings'),
        api.post_process(_tricium_outputs_json, [{
            'category': 'ClangTidy/tidy-is-angry',
            'path': 'path/to/some/cc/file.h',
            'message':
                'grrr '
                '(https://clang.llvm.org/extra/clang-tidy/checks/tidy/is-angry'
                '.html)'
                '\n\nExpanded from path/to/some/cc/file0.cpp:2' + suffix,
            'startLine': 3,
        }]),
        api.post_process(post_process.DropExpectation),
    )

  yield api.test(
      'diagnostic_use_after_move',
      with_patch(affected_files=['path/to/some/cc/file.cpp']),
      api.reclient.properties(),
      api.step_data(
          'clang-tidy.generate-warnings.read tidy output',
          api.file.read_json({
              'diagnostics': [{
                  'file_path': 'path/to/some/cc/file.cpp',
                  'line_number': 2,
                  'diag_name': 'bugprone-use-after-move',
                  'message': 'base message',
                  'replacements': [],
                  'notes': [
                      {
                          'message': 'clang-tidy go brrrrr',
                          'line_number': 123,
                          'file_path': 'path/to/some/cc/file.cpp',
                          'expansion_locs': [],
                      },
                      {
                          'message': 'move occurred here',
                          'line_number': 321,
                          'file_path': 'path/to/some/cc/file.cpp',
                          'expansion_locs': [],
                      },
                  ],
                  'expansion_locs': [],
              },]
          })),
      api.post_process(post_process.StepSuccess,
                       'clang-tidy.generate-warnings'),
      api.post_process(_tricium_outputs_json, [
          {
              'category': 'ClangTidy/bugprone-use-after-move',
              'path': 'path/to/some/cc/file.cpp',
              'message': 'base message '
                         '(https://clang.llvm.org/extra/clang-tidy/checks/'
                         'bugprone/use-after-move.html)',
              'startLine': 2,
          },
          {
              'category': 'ClangTidy/bugprone-use-after-move',
              'path': 'path/to/some/cc/file.cpp',
              'message': 'A `move` operation occurred here, which caused '
                         "'base message' at path/to/some/cc/file.cpp:2 "
                         '(https://clang.llvm.org/extra/clang-tidy/checks/'
                         'bugprone/use-after-move.html)',
              'startLine': 321,
          },
      ]),
      api.post_process(post_process.DropExpectation),
  )
