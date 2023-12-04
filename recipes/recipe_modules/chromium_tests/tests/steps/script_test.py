# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import (DropExpectation, LogContains, MustRun,
                                        StepCommandContains, StepTextEquals)

from RECIPE_MODULES.build.chromium_tests import steps

DEPS = [
    'build',
    'chromium',
    'chromium_tests',
    'presentation_utils',
    'recipe_engine/assertions',
    'recipe_engine/buildbucket',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/step',
    'test_utils',
]


def RunSteps(api):
  api.chromium.set_config('chromium')

  test_spec = steps.ScriptTestSpec.create(
      'script_test',
      script='script.py',
      all_compile_targets={'script.py': ['compile_target']},
      script_args=['some', 'args'])
  test = test_spec.get_test(api.chromium_tests)
  api.assertions.assertEqual(test.option_flags, steps.TestOptionFlags.create())

  try:
    test.run('')
  finally:
    api.step('details', [])
    api.step.active_result.presentation.logs['details'] = [
        'compile_targets: {!r}'.format(test.compile_targets()),
        'uses_local_devices: {!r}'.format(test.uses_local_devices),
    ]


def GenTests(api):
  yield api.test(
      'basic',
      api.chromium.ci_build(
          builder_group='test_group',
          builder='test_buildername',
      ),
      api.post_process(StepCommandContains, 'script_test', [
          'vpython3',
          'None/testing/scripts/script.py',
      ]),
      api.post_process(StepCommandContains, 'script_test', [
          '--args',
          '["some", "args"]',
      ]),
      api.post_process(LogContains, 'details', 'details',
                       ["compile_targets: ['compile_target']"]),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'invalid_results',
      api.chromium.ci_build(
          builder_group='test_group',
          builder='test_buildername',
      ),
      api.override_step_data('script_test', api.json.output({})),
      api.post_process(MustRun,
                       'script_test with suffix  had an invalid result'),
      api.expect_status('FAILURE'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'failure',
      api.chromium.ci_build(
          builder_group='test_group',
          builder='test_buildername',
      ),
      api.override_step_data(
          'script_test',
          api.json.output({
              'valid': True,
              'failures': ['TestOne']
          })),
      api.post_process(StepTextEquals, 'script_test',
                       '<br/>failures:<br/>TestOne<br/>'),
      api.post_process(DropExpectation),
  )
