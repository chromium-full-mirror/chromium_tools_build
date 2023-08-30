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
  api.m.chromium_rts.rts_setting = 'rts-chromium'
  api.m.chromium_rts.rts_recall = .95
  api.m.chromium_rts.inverted_rts = False

  api.m.chromium_rts.setup_tests(tests)
  assert (tests[0].is_rts)
  assert (api.m.chromium_rts.inverted_rts == False)

  api.m.chromium_rts.inverted_rts = True
  api.m.chromium_rts.setup_tests(tests)
  assert (tests[0].is_inverted_rts)
  assert (api.m.chromium_rts.inverted_rts == True)

  mb_args = api.m.chromium_rts.mb_args()
  assert (mb_args[0] == '--rts')
  assert (mb_args[1] == 'rts-chromium')
  assert (mb_args[2] == '--rts-target-change-recall')
  assert (mb_args[3] == '0.95')


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
