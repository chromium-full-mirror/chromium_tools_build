# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
    chromium,
    chromium_checkout,
    chromium_tests,
    chromium_tests_builder_config,
)
from RECIPE_MODULES.recipe_engine import path


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_checkout: chromium_checkout.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  path: path.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API


def RunSteps(api: DEPS):
  api.chromium.set_config('chromium')
  api.chromium_checkout.set_paths(api.path.cache_dir / 'builder', 'src')
  source_dir = api.chromium_checkout.source_dir
  build_dir = api.chromium.default_build_dir(source_dir)
  api.chromium_tests.find_swarming_command_lines('chromium', build_dir)


def GenTests(api: TEST_DEPS):
  yield api.test(
      'no_build_dir',
      api.chromium.generic_build(
          builder_group='fake-group',
          builder='fake-tester',
      ),
      api.post_process(post_process.DropExpectation),
  )
