# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

DEPS = [
    'recipe_engine/buildbucket',
    'recipe_engine/cipd',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/step',
    'v8',
    'v8_auto_roller',
]

RETSAM = 'retsam'[::-1]


def RunSteps(api):
  autoroller_config = {
      'target_config': {
          'solution_name': 'v8',
          'project_name': 'v8/v8',
          'account': 'v8@example.com',
      },
      'subject': 'New rolls',
      'reviewers': [
          'anybody@chromium.org',
          'ciciobello@chromium.org',
      ],
      'roll_test262': True,
      'regular_deps_roller': False,
  }

  api.v8_auto_roller.setup(autoroller_config)

  api.v8_auto_roller.test262_roll(autoroller_config)

  return api.v8_auto_roller.report_result()


def GenTests(api):
  git_cl_info = """remote:
remote:   https://chromium-review.googlesource.com/c/chromium/tools/build/+/3840339 [v8] Remove deprecated roll recipe [WIP]
remote:"""

  yield (api.test('default') + api.override_step_data(
      'Update test262 import deps.Update Test262 status file.',
      api.raw_io.stream_output_text('range 1..3'),
  ) + api.override_step_data(
      'Update test262 import deps.gclient get test262 revision',
      api.raw_io.stream_output_text('345'),
  ) + api.override_step_data(
      'Update test262 import deps.git status',
      api.raw_io.stream_output_text('some difference', stream='stdout'),
  ) + api.override_step_data(
      'Update test262 import deps.git cl',
      api.raw_io.stream_output_text(git_cl_info, stream='stdout'),
  ) + api.v8.filter_exclude('Setup.ensure builder cache dir'))
