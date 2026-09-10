# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import profiles


@dataclass
class DEPS(RecipeScriptApi):
  profiles: profiles.API


def RunSteps(api: DEPS):
  api.profiles.find_merge_errors()


def GenTests(api: RecipeTestApi):

  yield api.test(
    'basic',
    api.post_process(post_process.MustRun, 'Finding profile merge errors'),
    api.post_process(post_process.DropExpectation),
  )
