# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import flakiness
from RECIPE_MODULES.recipe_engine import assertions


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  flakiness: flakiness.API


def RunSteps(api: DEPS):
  test_data = [
    {
      'test_id': 'ninja://some/test:module/TestSuite.test_a',
      'variant_hash': 'test_a',
    }
  ]
  tests = api.flakiness.process_precomputed_test_data(test_data)
  api.assertions.assertEqual(len(tests), 1)
  api.assertions.assertTrue(
    ('ninja://some/test:module/TestSuite.test_a', 'test_a') in tests
  )


def GenTests(api: RecipeTestApi):
  yield api.test(
    'basic',
    api.post_process(post_process.DropExpectation),
  )
