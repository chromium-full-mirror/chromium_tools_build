# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

DEPS = [
    'chromium',
    'chromium_rts',
    'chromium_tests',
    'recipe_engine/properties',
    'recipe_engine/step',
]

from recipe_engine import post_process

from RECIPE_MODULES.build.chromium_tests import steps


def RunSteps(api):
  tests = [
      steps.MockTestSpec.create('MockTest',
                                supports_rts=True).get_test(api.chromium_tests),
  ]
  api.m.chromium_rts.rts_model = 'smart-test-selection'

  api.m.chromium_rts.setup_tests(tests)
  assert (tests[0].is_rts)

  api.m.chromium_rts.setup_tests(tests)

  mb_args = api.m.chromium_rts.mb_args()
  assert (mb_args[0] == '--rts-model')
  assert (mb_args[1] == 'smart-test-selection')


def GenTests(api):
  yield api.test(
      'basic',
      api.post_process(post_process.DropExpectation),
  )

  # RTS on dry run causes builds to be compatible for different run modes and
  # can only be determined when the individual tests are set
  yield api.test(
      'dry_run_rts',
      api.chromium.try_build(experiments=['chromium_rts.dry_run_rts']),
      api.post_process(post_process.DropExpectation),
  )
