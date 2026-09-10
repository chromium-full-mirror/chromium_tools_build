# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine.post_process import DropExpectation, StepCommandContains

from RECIPE_MODULES.build.chromium_tests.steps import ResultDB

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium, chromium_android
from RECIPE_MODULES.recipe_engine import buildbucket, path


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  chromium: chromium.API
  chromium_android: chromium_android.API
  path: path.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API


def RunSteps(api: DEPS):
  api.chromium.set_config('chromium')
  api.chromium_android.set_config('base_config')
  source_dir = api.path.cache_dir / 'builder/src'
  build_dir = source_dir / 'out/Release'
  api.chromium_android.run_test_suite(
    source_dir, build_dir, 'test_suite', shard_timeout=1200
  )
  api.chromium_android.run_test_suite(
    source_dir,
    build_dir,
    'test_suite-with-rdb',
    resultdb=ResultDB.create(enable=True),
  )


def GenTests(api: TEST_DEPS):
  yield api.test(
    'basic',
    api.buildbucket.try_build(),
    api.post_process(
      StepCommandContains,
      'test_suite',
      [
        '-t',
        '1200',
      ],
    ),
    api.post_process(
      StepCommandContains,
      'test_suite-with-rdb',
      [
        'rdb',
        'stream',
        '-tag',
        'step_name:test_suite-with-rdb',
      ],
    ),
    api.post_process(DropExpectation),
  )
