# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine.post_process import DropExpectation, StepEnvContains

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium
from RECIPE_MODULES.recipe_engine import (
  context,
  path,
  platform,
  properties,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  context: context.API
  path: path.API
  platform: platform.API
  properties: properties.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  platform: platform.TEST_API


def RunSteps(api: DEPS):
  api.chromium.set_config('chromium', TARGET_PLATFORM='mac')

  source_dir = api.path.cache_dir / 'builder/src'

  with api.context(env=api.chromium.get_env(source_dir)):
    api.step('test', ['echo', 'foo'])


def GenTests(api: TEST_DEPS):

  yield api.test(
    'basic',
    api.platform('mac', 64),
    api.post_process(DropExpectation),
  )
