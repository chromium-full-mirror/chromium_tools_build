# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process
from recipe_engine.recipe_api import Property

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import isolate
from RECIPE_MODULES.recipe_engine import path


@dataclass
class DEPS(RecipeScriptApi):
  isolate: isolate.API
  path: path.API


def RunSteps(api: DEPS):
  api.isolate.isolate(
    'isolate', api.path.cache_dir / 'builder' / 'src' / 'test.isolate'
  )


def GenTests(api: RecipeTestApi):
  yield api.test(
    'basic',
    api.post_process(post_process.DropExpectation),
  )
