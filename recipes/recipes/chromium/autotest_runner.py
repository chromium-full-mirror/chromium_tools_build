# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Triggers building and running tests for the Autotest script"""

from recipe_engine.recipe_api import RecipeApi
from recipe_engine.recipe_test_api import RecipeTestApi

DEPS = [
    'recipe_engine/step',
]


def RunSteps(api: RecipeApi):
  api.step('hello world', ['echo', 'Hello', 'Autotest!'])


def GenTests(api: RecipeTestApi):
  yield api.test('basic')
