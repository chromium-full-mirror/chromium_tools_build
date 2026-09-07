# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from PB.recipe_modules.recipe_engine.led.properties import InputProperties

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium, chromium_tests
from RECIPE_MODULES.recipe_engine import led, properties


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_tests: chromium_tests.API
  led: led.API
  properties: properties.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API
  properties: properties.TEST_API


def RunSteps(api: DEPS):
  api.chromium_tests.print_link_to_results()


def GenTests(api: TEST_DEPS):
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
