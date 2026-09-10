# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import flakiness
from RECIPE_MODULES.recipe_engine import assertions


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  flakiness: flakiness.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  flakiness: flakiness.TEST_API


def RunSteps(api: DEPS):
  id_tests = api.flakiness.check_for_flakiness
  api.assertions.assertEqual(id_tests, True)


def GenTests(api: TEST_DEPS):

  yield api.test(
    'basic',
    api.flakiness(check_for_flakiness=True),
    api.post_process(post_process.DropExpectation),
  )
