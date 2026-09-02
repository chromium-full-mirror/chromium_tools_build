# Copyright 2018 The Chromium Authors. All rights reserved.
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


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API


def RunSteps(api: DEPS):
  source_dir = api.path.cache_dir / 'builder/src'
  version = api.chromium.get_version(source_dir)
  api.assertions.assertEqual(version, {
      'MAJOR': '123',
      'MINOR': '1',
      'BUILD': '9876',
      'PATCH': '2',
  })


def GenTests(api: TEST_DEPS):
  yield api.test(
      'override_version',
      api.chromium.override_version(major=123, minor=1, build=9876, patch=2),
      api.post_process(post_process.DropExpectation),
  )
