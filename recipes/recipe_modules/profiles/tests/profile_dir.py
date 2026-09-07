# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import profiles
from RECIPE_MODULES.recipe_engine import assertions


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  profiles: profiles.API


def RunSteps(api: DEPS):

  api.assertions.assertFalse(api.profiles._root_profile_dir)
  api.profiles.profile_dir()

  api.assertions.assertTrue(api.profiles._root_profile_dir)
  api.assertions.assertTrue(api.profiles.profile_dir())

  api.profiles.profile_dir(identifier='random_key')
  api.assertions.assertTrue(api.profiles.profile_subdirs)


def GenTests(api: RecipeTestApi):

  yield api.test(
      'basic',
      api.post_process(post_process.DropExpectation),
  )
