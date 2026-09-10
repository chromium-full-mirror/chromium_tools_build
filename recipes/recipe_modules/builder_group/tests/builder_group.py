# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import builder_group
from RECIPE_MODULES.recipe_engine import assertions, properties


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  builder_group: builder_group.API
  properties: properties.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  builder_group: builder_group.TEST_API


def RunSteps(api: DEPS):
  api.assertions.assertEqual(api.builder_group.for_current, 'current-group')
  api.assertions.assertEqual(api.builder_group.for_parent, 'parent-group')
  api.assertions.assertEqual(api.builder_group.for_target, 'target-group')


def GenTests(api: TEST_DEPS):
  yield api.test(
    'full',
    api.builder_group.for_current('current-group'),
    api.builder_group.for_parent('parent-group'),
    api.builder_group.for_target('target-group'),
    api.post_process(post_process.DropExpectation),
  )
