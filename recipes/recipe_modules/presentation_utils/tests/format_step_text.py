# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import presentation_utils
from RECIPE_MODULES.recipe_engine import properties


@dataclass
class DEPS(RecipeScriptApi):
  presentation_utils: presentation_utils.API
  properties: properties.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  properties: properties.TEST_API


def RunSteps(api: DEPS):
  data = api.properties['data']
  api.presentation_utils.format_step_text(data)


def GenTests(api: TEST_DEPS):
  yield api.test(
    'basic',
    api.properties(data=[('header', 'body')]),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'too_many_elements',
    api.properties(data=[('too', 'many', 'elements')]),
    api.expect_exception('AssertionError'),
    api.post_process(post_process.DropExpectation),
  )
