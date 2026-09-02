# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from RECIPE_MODULES.build import chromium_types

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium
from RECIPE_MODULES.recipe_engine import path


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  path: path.API


def RunSteps(api: DEPS):
  api.chromium.set_config('chromium')
  source_dir = api.path.cache_dir / 'builder/src'
  build_dir = source_dir / 'out/Release'
  api.chromium.mb_isolate_everything(
      source_dir,
      build_dir,
      chromium_types.BuilderId.create_for_group('test-group', 'test-builder'),
  )


def GenTests(api: RecipeTestApi):
  yield api.test(
      'basic',
      api.post_process(post_process.StepCommandRE, 'generate .isolate files', [
          'python3', '-u', r'.*/tools/mb/mb\.py', 'isolate-everything', '-m',
          'test-group', '-b', 'test-builder', '--config-file',
          r'.*/tools/mb/mb_config\.pyl', r'\[CACHE\]/builder/src/out/Release'
      ]),
      api.post_process(post_process.DropExpectation),
  )
