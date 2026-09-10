# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import swarming_client


@dataclass
class DEPS(RecipeScriptApi):
  swarming_client: swarming_client.API


def RunSteps(api: DEPS):
  _ = api.swarming_client.path


def GenTests(api: RecipeTestApi):
  yield api.test(
    'basic',
  )
