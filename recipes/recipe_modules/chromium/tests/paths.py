# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium
from RECIPE_MODULES.recipe_engine import assertions, path


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  chromium: chromium.API
  path: path.API


def RunSteps(api: DEPS):
  api.chromium.set_config('chromium')

  source_dir = api.path.cleanup_dir

  api.assertions.assertEqual(
    api.chromium.targets_spec_dir(source_dir), source_dir / 'testing/buildbot'
  )
  api.assertions.assertEqual(
    api.chromium.analyze_config_path(source_dir),
    source_dir / 'testing/buildbot/trybot_analyze_config.json',
  )


def GenTests(api: RecipeTestApi):
  yield api.test(
    'basic',
    api.post_process(post_process.DropExpectation),
  )
