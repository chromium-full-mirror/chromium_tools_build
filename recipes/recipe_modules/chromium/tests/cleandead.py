# Copyright 2023 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium, siso
from RECIPE_MODULES.recipe_engine import path, properties


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  path: path.API
  properties: properties.API
  siso: siso.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  siso: siso.TEST_API


def RunSteps(api: DEPS):
  api.chromium.set_config(
    api.properties.get('chromium_config', 'chromium_clang'),
    TARGET_PLATFORM=api.properties.get('target_platform', 'linux'),
    TARGET_CROS_BOARDS=api.properties.get('target_cros_boards'),
  )
  source_dir = api.path.cache_dir / 'builder/src'
  build_dir = source_dir / 'out/Release'
  return api.chromium.cleandead(source_dir, build_dir)


def GenTests(api: TEST_DEPS):
  step_name = 'cleandead'
  yield api.test(
    'basic',
    api.post_check(
      post_process.StepCommandRE,
      step_name,
      ['.*/ninja', '-C', '.*', '-t', 'cleandead'],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'siso',
    api.siso.properties(),
    api.post_check(
      post_process.StepCommandRE,
      step_name,
      ['.*/siso', 'ninja', '-C', '.*', '-t', 'cleandead'],
    ),
    api.post_process(post_process.DropExpectation),
  )
