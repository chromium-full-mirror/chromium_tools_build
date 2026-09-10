# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import profiles
from RECIPE_MODULES.recipe_engine import path


@dataclass
class DEPS(RecipeScriptApi):
  path: path.API
  profiles: profiles.API


def RunSteps(api: DEPS):
  # coverage only
  api.profiles.source_dir = api.path.cleanup_dir
  _ = api.profiles.merge_scripts_dir
  _ = api.profiles.merge_steps_script
  _ = api.profiles.merge_results_script
  _ = api.profiles.profile_subdirs
  _ = api.profiles.llvm_profdata_exec


def GenTests(api: RecipeTestApi):

  yield api.test(
    'properties',
    api.post_process(post_process.DropExpectation),
  )
