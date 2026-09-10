# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import builder_group, chromium
from RECIPE_MODULES.recipe_engine import assertions, buildbucket, properties


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  buildbucket: buildbucket.API
  builder_group: builder_group.API
  chromium: chromium.API
  properties: properties.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API


def RunSteps(api: DEPS):
  api.assertions.assertNotEqual(
    len(api.buildbucket.build.input.gerrit_changes), 0
  )
  api.assertions.assertEqual(api.builder_group.for_current, 'fake-group')


def GenTests(api: TEST_DEPS):
  yield api.test(
    'basic',
    api.chromium.try_build(builder_group='fake-group'),
    api.post_process(post_process.DropExpectation),
  )
