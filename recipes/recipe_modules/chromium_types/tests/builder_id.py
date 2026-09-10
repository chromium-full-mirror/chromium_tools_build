# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from RECIPE_MODULES.build.chromium_types import BuilderId

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.recipe_engine import assertions


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API


def RunSteps(api: DEPS):
  builder_id = BuilderId.create_for_group('fake-group', 'fake-builder')
  api.assertions.assertEqual(str(builder_id), 'fake-group:fake-builder')


def GenTests(api: RecipeTestApi):
  yield api.test(
    'full',
    api.post_process(post_process.DropExpectation),
  )
