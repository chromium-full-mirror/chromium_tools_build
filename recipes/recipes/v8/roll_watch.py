# Copyright 2023 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.recipe_api import Property
from recipe_engine.post_process import DropExpectation

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import v8_roll_watcher


@dataclass
class DEPS(RecipeScriptApi):
  v8_roll_watcher: v8_roll_watcher.API

PROPERTIES = {
    'watched_rollers': Property(
            help='Roller descriptors',
            default=[],
            kind=list
    )
}


def RunSteps(api: DEPS, watched_rollers):
  return api.v8_roll_watcher.process_rollers(watched_rollers)


def GenTests(api: RecipeTestApi):
  # Minimal test for recipe coverage (still redundant with module tests).
  yield api.test('basic') + api.post_process(DropExpectation)
