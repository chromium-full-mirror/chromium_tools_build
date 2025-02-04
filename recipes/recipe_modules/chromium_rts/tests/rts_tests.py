# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

DEPS = [
    'chromium',
    'chromium_rts',
    'chromium_tests',
    'recipe_engine/properties',
    'recipe_engine/step',
    'recipe_engine/swarming',
]

from PB.recipes.build.chromium.compilator import InputProperties
from PB.recipe_modules.recipe_engine.led.properties import InputProperties as InputPropertiesLed
from recipe_engine import post_process

from RECIPE_MODULES.build.chromium_tests import steps

_ORCHESTRATOR_BUILD_ID = 1234
_COMPILATOR_BUILD_ID = 5678


def RunSteps(api):
  test_target = api.properties.get('test_target', 'browser_tests')
  tests = [
      steps.MockTestSpec.create(test_target,
                                supports_rts=True).get_test(api.chromium_tests),
  ]
  api.m.chromium_rts.rts_model = 'smart-test-selection'

  api.m.chromium_rts.setup_tests(tests)
  api.m.chromium_rts.trigger_test_selection(tests)
  if test_target == 'browser_tests':
    assert (tests[0].is_rts)
  else:
    assert not (tests[0].is_rts)
  api.m.chromium_rts.setup_tests(tests)

  mb_args = api.m.chromium_rts.mb_args()
  assert (mb_args[0] == '--rts-model')
  assert (mb_args[1] == 'smart-test-selection')


def GenTests(api):
  # RTS on dry run causes builds to be compatible for different run modes and
  # can only be determined when the individual tests are set
  yield api.test(
      'rts_basic',
      api.chromium.try_build(
          experiments=['chromium_rts.rts'], build_id=_COMPILATOR_BUILD_ID),
      api.post_process(
          post_process.MustRun,
          'fetch api key and trigger test selection.Trigger test selection for browser_tests'
      ),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'rts_basic_not_browser_tests',
      api.chromium.try_build(
          experiments=['chromium_rts.rts'], build_id=_COMPILATOR_BUILD_ID),
      api.properties(test_target='unit_tests'),
      api.post_process(
          post_process.DoesNotRun,
          'fetch api key and trigger test selection.Trigger test selection for unit_tests'
      ),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'rts_basic_with_orchestrator',
      api.chromium.try_build(
          experiments=['chromium_rts.rts'],
          build_id=_COMPILATOR_BUILD_ID,
          ancestor_ids=[_ORCHESTRATOR_BUILD_ID]),
      api.properties(
          InputProperties(
              orchestrator=InputProperties.Orchestrator(
                  builder_name='fake-orchestrator',
                  builder_group='fake-try-group'))),
      api.post_process(
          post_process.MustRun,
          'fetch api key and trigger test selection.Trigger test selection for browser_tests'
      ),
      api.post_process(
          post_process.MustRun,
          'fetch api key and trigger test selection.Get orchestrator build'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'rts_basic_with_led',
      api.chromium.try_build(
          experiments=['chromium_rts.rts'],
          build_id=_COMPILATOR_BUILD_ID,
          ancestor_ids=[_ORCHESTRATOR_BUILD_ID]),
      api.properties(**{
          '$recipe_engine/led': InputPropertiesLed(led_run_id='some-led-run'),
      }),
      api.swarming.properties(task_id='some-task-id'),
      api.post_process(
          post_process.MustRun,
          'fetch api key and trigger test selection.Trigger test selection for browser_tests'
      ),
      api.post_process(post_process.DropExpectation),
  )
