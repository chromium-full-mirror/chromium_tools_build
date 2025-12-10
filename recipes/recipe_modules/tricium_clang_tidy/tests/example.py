# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.go.chromium.org.luci.common.proto.findings import findings as findings_pb
from RECIPE_MODULES.build.tricium_clang_tidy import _clang_tidy_path
from recipe_engine import post_process

DEPS = [
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/proto',
    'recipe_engine/step',
    'tricium_clang_tidy',
    'chromium',
]

def RunSteps(api):
  cache_dir = api.path.cache_dir
  source_dir = cache_dir / 'builder' / 'src'
  with api.context(cwd=cache_dir):
    # file_paths should be kept in sync with the paths used in test below.
    findings = api.tricium_clang_tidy.lint_source_files(
        source_dir,
        output_dir=cache_dir / 'out',
        file_paths=[cache_dir.joinpath('src', 'path/to/some/cc/file.cpp')],
        skip_checks=api.properties.get('skip_checks', None),
        is_windows=api.properties['is_windows'])
    if findings:
      api.step.empty(
          'dump findings',
          log_text=api.proto.encode(
              findings_pb.Findings(findings=findings), 'JSONPB'),
          log_name='findings.json')

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

  def _get_uploaded_findings(steps):
    if 'dump findings' not in steps or ('findings.json'
                                        not in steps['dump findings'].logs):
      return []
    findings_json = steps['dump findings'].logs['findings.json']
    return api.proto.decode(findings_json, findings_pb.Findings,
                            'JSONPB').findings

  def _has_no_finding(check, steps):
    check(not _get_uploaded_findings(steps))

  def _has_finding(check, steps, finding):
    findings = _get_uploaded_findings(steps)
    check(findings)
    if findings:
      check(finding in findings)

  gerrit_change_ref = findings_pb.Location.GerritChangeReference(
      host='chromium-review.googlesource.com',
      project='chromium/src',
      change=456789,
      patchset=12)

  yield api.test(
      'no_files',
      with_patch(affected_files=[]),
      api.chromium.try_build(),
      api.post_process(post_process.DoesNotRun, 'clang-tidy'),
      api.post_process(_has_no_finding),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'no_analysis_non_cpp',
      with_patch(affected_files=['some/cc/file.txt']),
      api.chromium.try_build(),
      api.post_process(post_process.DoesNotRun, 'clang-tidy'),
      api.post_process(_has_no_finding),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'removed_file',
      with_patch(
          affected_files=['path/to/some/cc/file.cpp'], auto_exist_files=False),
      api.chromium.try_build(),
      api.post_process(post_process.DoesNotRun, 'clang-tidy'),
      api.post_process(_has_no_finding),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'analyze_cpp_timed_out_files',
      with_patch(affected_files=['path/to/some/cc/file.cpp']),
      api.chromium.try_build(),
      api.step_data('clang-tidy.generate-warnings.read tidy output',
                    api.file.read_json({'timed_out_src_files': ['oh/no.cpp']})),
      api.post_process(post_process.StepWarning,
                       'clang-tidy.generate-warnings'),
      api.post_process(
          _has_finding,
          findings_pb.Finding(
              category='clang-tidy',
              location=findings_pb.Location(
                  gerrit_change_ref=gerrit_change_ref, file_path='oh/no.cpp'),
              message='warning: clang-tidy timed out on this file; '
              'issuing diagnostics is impossible.',
              severity_level=findings_pb.Finding.SEVERITY_LEVEL_WARNING,
              url="https://cr-buildbucket.appspot.com/build/8945511751514863184",
          )),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'analyze_cpp_failed_files',
      with_patch(affected_files=['path/to/some/cc/file.cpp']),
      api.chromium.try_build(),
      api.step_data(
          'clang-tidy.generate-warnings.read tidy output',
          api.file.read_json({'failed_src_files': ['path/to/some/cc/file.cpp']
                             })),
      api.post_process(post_process.StepWarning,
                       'clang-tidy.generate-warnings'),
      api.post_process(_has_no_finding),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'analyze_cpp_failed_tidy_files',
      with_patch(affected_files=['path/to/some/cc/file.cpp']),
      api.chromium.try_build(),
      api.step_data(
          'clang-tidy.generate-warnings.read tidy output',
          api.file.read_json(
              {'failed_tidy_files': ['path/to/some/cc/file.cpp']})),
      api.post_process(post_process.StepWarning,
                       'clang-tidy.generate-warnings'),
      api.post_process(_has_no_finding),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'analyze_cpp',
      with_patch(affected_files=['path/to/some/cc/file.cpp']),
      api.chromium.try_build(),
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
          _has_finding,
          findings_pb.Finding(
              category='clang-tidy',
              location=findings_pb.Location(
                  gerrit_change_ref=gerrit_change_ref,
                  file_path='path/to/some/cc/file.cpp',
                  range=findings_pb.Location.Range(
                      start_line=2,
                      end_line=2,
                  )),
              message='check: super-cool-diag\n\nhello, world 1 '
              '(https://clang.llvm.org/extra/clang-tidy/checks/super/'
              'cool-diag.html)\n\n(Note: You can add '
              '`Skip-Clang-Tidy-Checks: super-cool-diag` footer to '
              'the CL description to skip the check)',
              severity_level=findings_pb.Finding.SEVERITY_LEVEL_WARNING,
              url="https://cr-buildbucket.appspot.com/build/8945511751514863184",
          )),
      api.post_process(
          _has_finding,
          findings_pb.Finding(
              category='clang-tidy',
              location=findings_pb.Location(
                  gerrit_change_ref=gerrit_change_ref,
                  file_path='path/to/some/cc/file.cpp',
                  range=findings_pb.Location.Range(
                      start_line=50,
                      end_line=50,
                  )),
              message='check: moderately-cool-diag\n\nhello, world '
              '(https://clang.llvm.org/extra/clang-tidy/checks/moderately/'
              'cool-diag.html)\n\n(Note: You can add '
              '`Skip-Clang-Tidy-Checks: moderately-cool-diag` footer to '
              'the CL description to skip the check)',
              severity_level=findings_pb.Finding.SEVERITY_LEVEL_WARNING,
              url="https://cr-buildbucket.appspot.com/build/8945511751514863184",
          )),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'analyze_cpp_check_name_without_hyphen',
      with_patch(affected_files=['path/to/some/cc/file.cpp']),
      api.chromium.try_build(),
      api.step_data(
          'clang-tidy.generate-warnings.read tidy output',
          api.file.read_json({
              'diagnostics': [{
                  'file_path': 'path/to/some/cc/file.cpp',
                  'line_number': 10,
                  'diag_name': 'readability',
                  'message': 'a simple message',
                  'replacements': [],
                  'expansion_locs': [],
              },]
          })),
      api.post_process(post_process.StepSuccess,
                       'clang-tidy.generate-warnings'),
      api.post_process(
          _has_finding,
          findings_pb.Finding(
              category='clang-tidy',
              location=findings_pb.Location(
                  gerrit_change_ref=gerrit_change_ref,
                  file_path='path/to/some/cc/file.cpp',
                  range=findings_pb.Location.Range(
                      start_line=10,
                      end_line=10,
                  )),
              message='check: readability\n\na simple message '
              '(https://clang.llvm.org/extra/clang-tidy/checks/'
              'readability.html)\n\n(Note: You can add '
              '`Skip-Clang-Tidy-Checks: readability` footer to '
              'the CL description to skip the check)',
              severity_level=findings_pb.Finding.SEVERITY_LEVEL_WARNING,
              url="https://cr-buildbucket.appspot.com/build/8945511751514863184",
          )),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'analyze_skip_checks',
      with_patch(affected_files=['path/to/some/cc/file.cpp']),
      api.properties(skip_checks=['super-cool-diag', 'moderately-cool-diag']),
      api.chromium.try_build(),
      api.step_data('clang-tidy.generate-warnings.read tidy output',
                    api.file.read_json({'diagnostics': []})),
      api.post_process(post_process.StepSuccess,
                       'clang-tidy.generate-warnings'),
      api.post_process(
          post_process.StepCommandContains,
          'clang-tidy.generate-warnings.tricium_clang_tidy_script.py',
          ['--tidy_checks=-super-cool-diag,-moderately-cool-diag']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'analyze_cpp_windows',
      with_patch(affected_files=['path/to/some/cc/file.cpp'], is_windows=True),
      api.chromium.try_build(),
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
          _has_finding,
          findings_pb.Finding(
              category='clang-tidy',
              location=findings_pb.Location(
                  gerrit_change_ref=gerrit_change_ref,
                  file_path='path/to/some/cc/file.cpp',
                  range=findings_pb.Location.Range(
                      start_line=2,
                      end_line=2,
                  )),
              message='check: super-cool-diag\n\nhello, world 1 '
              '(https://clang.llvm.org/extra/clang-tidy/checks/super/'
              'cool-diag.html)\n\n(Note: You can add '
              '`Skip-Clang-Tidy-Checks: super-cool-diag` footer to the CL '
              'description to skip the check)',
              severity_level=findings_pb.Finding.SEVERITY_LEVEL_WARNING,
              url="https://cr-buildbucket.appspot.com/build/8945511751514863184",
          )),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'only_warnings_and_errors_are_silenced',
      with_patch(affected_files=['path/to/some/cc/file.cpp']),
      api.chromium.try_build(),
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
      api.post_process(_has_no_finding),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'prefer_complaints_about_timeout_over_tidy_failures',
      with_patch(affected_files=['path/to/some/cc/file.cpp']),
      api.chromium.try_build(),
      api.step_data(
          'clang-tidy.generate-warnings.read tidy output',
          api.file.read_json({
              'timed_out_src_files': ['path/to/some/cc/file.cpp'],
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
          _has_finding,
          findings_pb.Finding(
              category='clang-tidy',
              location=findings_pb.Location(
                  gerrit_change_ref=gerrit_change_ref,
                  file_path='path/to/some/cc/file.cpp'),
              message='warning: clang-tidy timed out on this file; issuing '
              'diagnostics is impossible.',
              severity_level=findings_pb.Finding.SEVERITY_LEVEL_WARNING,
              url="https://cr-buildbucket.appspot.com/build/8945511751514863184",
          )),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'append_complaint_on_failure',
      with_patch(affected_files=['path/to/some/cc/file.cpp']),
      api.chromium.try_build(),
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
          _has_finding,
          findings_pb.Finding(
              category='clang-tidy',
              location=findings_pb.Location(
                  gerrit_change_ref=gerrit_change_ref,
                  file_path='path/to/some/cc/file.cpp'),
              message='warning: building this file or its dependencies failed; '
              'no diagnostics will be issued.',
              severity_level=findings_pb.Finding.SEVERITY_LEVEL_WARNING,
              url="https://cr-buildbucket.appspot.com/build/8945511751514863184",
          )),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'prefer_complaints_about_build_failures_over_tidy_ones',
      with_patch(affected_files=['path/to/some/cc/file.cpp']),
      api.chromium.try_build(),
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
          _has_finding,
          findings_pb.Finding(
              category='clang-tidy',
              location=findings_pb.Location(
                  gerrit_change_ref=gerrit_change_ref,
                  file_path='path/to/some/cc/file.cpp'),
              message='warning: building this file or its dependencies failed; '
              'no diagnostics will be issued.',
              severity_level=findings_pb.Finding.SEVERITY_LEVEL_WARNING,
              url="https://cr-buildbucket.appspot.com/build/8945511751514863184",
          )),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'append_complaint_on_tidy_failure',
      api.chromium.try_build(),
      with_patch(affected_files=['path/to/some/cc/file.cpp']),
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
          _has_finding,
          findings_pb.Finding(
              category='clang-tidy',
              location=findings_pb.Location(
                  gerrit_change_ref=gerrit_change_ref,
                  file_path='path/to/some/cc/file.cpp'),
              message='warning: clang-tidy failed on this file; no diagnostics '
              'will be issued.',
              severity_level=findings_pb.Finding.SEVERITY_LEVEL_WARNING,
              url="https://cr-buildbucket.appspot.com/build/8945511751514863184",
          )),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'diagnostic_suggestions',
      with_patch(affected_files=['path/to/some/cc/file.cpp']),
      api.chromium.try_build(),
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
      api.post_process(
          _has_finding,
          findings_pb.Finding(
              category='clang-tidy',
              location=findings_pb.Location(
                  gerrit_change_ref=gerrit_change_ref,
                  file_path='path/to/some/cc/file.cpp',
                  range=findings_pb.Location.Range(
                      start_line=2,
                      end_line=2,
                  )),
              message='check: tidy-is-angry\n\nhello, world '
              '(https://clang.llvm.org/extra/clang-tidy/checks/tidy/'
              'is-angry.html)\n\n(Note: You can add '
              '`Skip-Clang-Tidy-Checks: tidy-is-angry` footer to '
              'the CL description to skip the check)',
              severity_level=findings_pb.Finding.SEVERITY_LEVEL_WARNING,\
              url="https://cr-buildbucket.appspot.com/build/8945511751514863184",
              fixes=[
                  findings_pb.Fix(
                      replacements=[
                          findings_pb.Fix.Replacement(
                              location=findings_pb.Location(
                                  gerrit_change_ref=gerrit_change_ref,
                                  file_path='path/to/some/cc/file.cpp',
                                  range=findings_pb.Location.Range(
                                      start_line=1,
                                      end_line=2,
                                      start_column=0,
                                      end_column=1,
                                  )),
                              new_content='foo',
                          ),
                          findings_pb.Fix.Replacement(
                              location=findings_pb.Location(
                                  gerrit_change_ref=gerrit_change_ref,
                                  file_path='path/to/some/cc/file.cpp',
                                  range=findings_pb.Location.Range(
                                      start_line=3,
                                      end_line=4,
                                      start_column=5,
                                      end_column=5,
                                  )),
                              new_content='bar',
                          )
                      ],)
              ],
          )),
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
        api.chromium.try_build(),
        api.step_data('clang-tidy.generate-warnings.read tidy output',
                      api.file.read_json({'diagnostics': diags})),
        api.post_process(post_process.StepSuccess,
                         'clang-tidy.generate-warnings'),
        api.post_process(
            _has_finding,
            findings_pb.Finding(
                category='clang-tidy',
                location=findings_pb.Location(
                    gerrit_change_ref=gerrit_change_ref,
                    file_path='path/to/some/cc/file.h',
                    range=findings_pb.Location.Range(
                        start_line=3,
                        end_line=3,
                    )),
                message='check: tidy-is-angry\n\ngrrr '
                '(https://clang.llvm.org/extra/clang-tidy/checks/tidy/'
                'is-angry.html)\n\nExpanded from '
                'path/to/some/cc/file0.cpp:2' + suffix +
                '\n\n(Note: You can add `Skip-Clang-Tidy-Checks: tidy-is-angry`'
                ' footer to the CL description to skip the check)',
                severity_level=findings_pb.Finding.SEVERITY_LEVEL_WARNING,
                url="https://cr-buildbucket.appspot.com/build/8945511751514863184",
            )),
        api.post_process(post_process.DropExpectation),
    )

  yield api.test(
      'diagnostic_use_after_move',
      with_patch(affected_files=['path/to/some/cc/file.cpp']),
      api.chromium.try_build(),
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
      api.post_process(
          _has_finding,
          findings_pb.Finding(
              category='clang-tidy',
              location=findings_pb.Location(
                  gerrit_change_ref=gerrit_change_ref,
                  file_path='path/to/some/cc/file.cpp',
                  range=findings_pb.Location.Range(
                      start_line=2,
                      end_line=2,
                  )),
              message='check: bugprone-use-after-move\n\nbase message '
              '(https://clang.llvm.org/extra/clang-tidy/checks/'
              'bugprone/use-after-move.html)\n\n(Note: You can add '
              '`Skip-Clang-Tidy-Checks: bugprone-use-after-move` footer to '
              'the CL description to skip the check)',
              severity_level=findings_pb.Finding.SEVERITY_LEVEL_WARNING,
              url="https://cr-buildbucket.appspot.com/build/8945511751514863184",
          )),
      api.post_process(
          _has_finding,
          findings_pb.Finding(
              category='clang-tidy',
              location=findings_pb.Location(
                  gerrit_change_ref=gerrit_change_ref,
                  file_path='path/to/some/cc/file.cpp',
                  range=findings_pb.Location.Range(
                      start_line=321,
                      end_line=321,
                  )),
              message='check: bugprone-use-after-move\n\n'
              'A `move` operation occurred here, which caused '
              "'base message' at path/to/some/cc/file.cpp:2 "
              '(https://clang.llvm.org/extra/clang-tidy/checks/'
              'bugprone/use-after-move.html)\n\n(Note: You can add '
              '`Skip-Clang-Tidy-Checks: bugprone-use-after-move` footer to '
              'the CL description to skip the check)',
              severity_level=findings_pb.Finding.SEVERITY_LEVEL_WARNING,
              url="https://cr-buildbucket.appspot.com/build/8945511751514863184",
          )),
      api.post_process(post_process.DropExpectation),
  )
