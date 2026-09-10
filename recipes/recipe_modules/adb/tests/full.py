# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import adb
from RECIPE_MODULES.recipe_engine import assertions, path, step


@dataclass
class DEPS(RecipeScriptApi):
  adb: adb.API
  assertions: assertions.API
  path: path.API
  step: step.API


def RunSteps(api: DEPS):
  source_dir = api.path.cache_dir / 'builder/src'
  default_adb_path = api.adb.default_adb_path(source_dir)
  api.assertions.assertEqual(
    default_adb_path,
    source_dir / 'third_party/android_sdk/public/platform-tools/adb',
  )

  api.adb.root_devices(source_dir / 'custom/adb/path')


def GenTests(api: RecipeTestApi):
  yield api.test('basic')
