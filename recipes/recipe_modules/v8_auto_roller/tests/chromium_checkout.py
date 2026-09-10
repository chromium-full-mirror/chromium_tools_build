# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine.post_process import (
  DropExpectation,
  StepCommandContains,
)

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import v8_auto_roller


@dataclass
class DEPS(RecipeScriptApi):
  v8_auto_roller: v8_auto_roller.API


def RunSteps(api: DEPS):
  api.v8_auto_roller.setup_target(
    'v8',
    'https://chromium.googlesource.com/v8/v8',
    requires_chromium_checkout=True,
  )


def GenTests(api: RecipeTestApi):
  yield api.test(
    'default',
    api.post_process(
      StepCommandContains,
      'Setup.bot_update',
      "cache_dir = '[CACHE]/git'\nsolutions = [{'deps_file': '.DEPS.git', 'managed': True, 'name': 'v8', 'url': 'https://chromium.googlesource.com/v8/v8'}, {'deps_file': '.DEPS.git', 'managed': True, 'name': 'src', 'url': 'https://chromium.googlesource.com/chromium/src.git'}]\ntarget_os = ['android', 'win']",
    ),
    api.post_process(DropExpectation),
  )
