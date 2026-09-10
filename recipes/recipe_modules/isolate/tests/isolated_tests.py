# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine.post_process import DropExpectation, LogContains

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import isolate
from RECIPE_MODULES.recipe_engine import properties, step


@dataclass
class DEPS(RecipeScriptApi):
  isolate: isolate.API
  properties: properties.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  properties: properties.TEST_API


def RunSteps(api: DEPS):
  api.step('isolated_tests', [])
  api.step.active_result.presentation.logs['details'] = [
    'isolated_tests: %r' % api.isolate.isolated_tests
  ]


def GenTests(api: TEST_DEPS):
  yield api.test(
    'basic',
    api.properties(
      swarm_hashes={
        'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff',
      }
    ),
    api.post_process(
      LogContains,
      'isolated_tests',
      'details',
      [
        "isolated_tests: {'base_unittests': "
        "'ffffffffffffffffffffffffffffffffffffffff'}",
      ],
    ),
    api.post_process(DropExpectation),
  )
