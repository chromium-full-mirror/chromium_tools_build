# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.recipe_engine import assertions


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API


from recipe_engine import post_process

from RECIPE_MODULES.build.chromium_tests.steps import SuccessReuseTest


def RunSteps(api: DEPS):
  test = SuccessReuseTest(None, None, None)
  api.assertions.assertFalse(test.is_enabled)


def GenTests(api: RecipeTestApi):
  yield api.test(
    'basic',
    api.post_process(post_process.DropExpectation),
  )
