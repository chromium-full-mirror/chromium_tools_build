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
  supports_rts = api.properties.get('supports_rts', True)
  tests = [
      steps.MockTestSpec.create('MockTest', supports_rts=supports_rts).get_test(
          api.chromium_tests),
  ]
  assert (not api.m.chromium_rts.enabled)
  api.m.chromium_rts.rts_model = 'smart-test-selection'
  assert (api.m.chromium_rts.enabled)

  api.m.chromium_rts.setup_tests(tests)
  api.m.chromium_rts.trigger_test_selection(tests)
  if supports_rts:
    assert (tests[0].is_rts)

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
          'fetch api key and trigger test selection.Trigger test selection'),
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
          'fetch api key and trigger test selection.Trigger test selection'),
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
          'fetch api key and trigger test selection.Trigger test selection'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'skip_test_selection',
      api.chromium.try_build(
          experiments=['chromium_rts.rts'], build_id=_COMPILATOR_BUILD_ID),
      api.properties(supports_rts=False),
      api.post_process(post_process.MustRun,
                       'No candidate test targets for smart test selection'),
      api.post_process(post_process.DropExpectation),
  )
