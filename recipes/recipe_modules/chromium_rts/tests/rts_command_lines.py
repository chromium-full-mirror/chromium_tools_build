# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

from RECIPE_MODULES.build.chromium_tests.steps import MockTestSpec

DEPS = [
    'chromium_rts',
    'chromium_tests',
    'recipe_engine/file',
    'recipe_engine/path',
    'recipe_engine/properties',
]


def RunSteps(api):
  spec = MockTestSpec.create(name='blink_web_tests', runs_on_swarming=True)
  test = spec.get_test(api.chromium_tests)
  messages = []
  api.chromium_rts.append_test_step_text(test, messages)
  assert not messages

  api.chromium_rts.set_swarming_test_execution_info(
      test, {'rts': {
          'blink_web_tests': ['/bin/rts_cmd']
      }})
  assert test.raw_cmd == ['/bin/rts_cmd']
  api.chromium_rts.append_test_step_text(test, messages)
  assert messages == [
      'Ran tests selected by Regression Test Selection (RTS).\n'
  ]
  api.chromium_rts.set_swarming_test_execution_info(test, None)


def GenTests(api):
  yield api.test(
      'set_swarming_test_execution_info',
      api.post_process(post_process.DropExpectation),
  )
