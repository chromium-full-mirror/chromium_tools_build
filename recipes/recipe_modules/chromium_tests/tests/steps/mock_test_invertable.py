# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium_tests
from RECIPE_MODULES.recipe_engine import properties, step


@dataclass
class DEPS(RecipeScriptApi):
  chromium_tests: chromium_tests.API
  properties: properties.API
  step: step.API

from recipe_engine.post_process import (DropExpectation)
from RECIPE_MODULES.build.chromium_tests import steps


def RunSteps(api: DEPS):
  test_spec = steps.MockTestSpec.create(
      name=api.properties.get('test_name', 'MockTest'))
  test_spec.get_test(api.chromium_tests)


def GenTests(api: RecipeTestApi):
  yield api.test(
      'basic',
      api.post_process(DropExpectation),
  )
