# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from PB.recipe_modules.recipe_engine.led.properties import InputProperties

DEPS = [
    'chromium',
    'chromium_tests',
    'recipe_engine/led',
    'recipe_engine/properties',
]


def RunSteps(api):
  api.chromium_tests.print_link_to_results()


def GenTests(api):
  yield api.test(
      'a-normal-build',
      api.chromium.ci_build(),
      api.post_process(post_process.DoesNotRun, 'test results link'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'a-led-build',
      api.properties(**{
          '$recipe_engine/led': InputProperties(led_run_id='some-led-run'),
      }),
      api.post_process(post_process.MustRun, 'test results link'),
      api.post_process(post_process.DropExpectation),
  )
