# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


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
  api.m.chromium_rts.rts_model = 'chromium-rts'

  api.m.chromium_rts.setup_tests(tests)
  assert (tests[0].is_rts)
  api.m.chromium_rts.setup_tests(tests)


def GenTests(api):
  yield api.test(
      'basic',
      api.chromium.try_build(),
      api.post_process(post_process.DropExpectation),
  )
