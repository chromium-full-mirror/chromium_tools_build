# Copyright 2023 The Chromium Authors
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


# TODO(sshrimp): replace these tests when non-orchestrator supports inverted
# quick run
def RunSteps(api):
  tests = [
      steps.MockTestSpec.create('MockTest',
                                supports_rts=True).get_test(api.chromium_tests),
  ]

  api.m.chromium_rts.setup_quickrun_tests(tests, 'rts-chromium', False)
  assert (tests[0].is_rts)
  api.m.chromium_rts.setup_quickrun_tests(tests, 'rts-chromium', True)
  assert (tests[0].is_inverted_rts)


def GenTests(api):
  yield api.test(
      'basic',
      api.post_process(post_process.DropExpectation),
  )

  # RTS on dry run causes builds to be compatible for different run modes and
  # can only be determined when the individual tests are set
  yield api.test(
      'dry_run_rts',
      api.chromium.try_build(experiments=['chromium_rts.dry_run_rts'],),
      api.post_process(post_process.DropExpectation),
  )
