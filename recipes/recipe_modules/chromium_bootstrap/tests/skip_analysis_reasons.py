# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium_bootstrap
from RECIPE_MODULES.recipe_engine import assertions, properties


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  chromium_bootstrap: chromium_bootstrap.API
  properties: properties.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium_bootstrap: chromium_bootstrap.TEST_API
  properties: properties.TEST_API


def RunSteps(api: DEPS):
  reasons = api.chromium_bootstrap.skip_analysis_reasons
  expected_reasons = api.properties['expected_reasons']
  api.assertions.assertEqual(reasons, expected_reasons)


def GenTests(api: TEST_DEPS):
  yield api.test(
    'basic',
    api.chromium_bootstrap.properties(skip_analysis_reasons=['foo', 'bar']),
    api.properties(expected_reasons=['foo', 'bar']),
    api.post_process(post_process.DropExpectation),
  )
