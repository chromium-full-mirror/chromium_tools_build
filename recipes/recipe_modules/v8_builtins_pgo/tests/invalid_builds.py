# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine.post_process import DropExpectation

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import v8_builtins_pgo
from RECIPE_MODULES.recipe_engine import buildbucket


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  v8_builtins_pgo: v8_builtins_pgo.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API


def RunSteps(api: DEPS):
  return api.v8_builtins_pgo.run(compilators=['x64'])


def GenTests(api: TEST_DEPS):
  yield api.test(
      'unsupported-bucket',
      api.buildbucket.ci_build(bucket='unsupported-bucket', revision=None),
      api.expect_exception('AssertionError'),
      api.post_process(DropExpectation),
  )
