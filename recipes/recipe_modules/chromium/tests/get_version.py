# Copyright 2017 The Chromium Authors. All rights reserved.
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
  source_dir = api.path.cache_dir / 'builder/src'

  version = api.chromium.get_version(source_dir)
  api.assertions.assertEqual(
    version,
    {
      'MAJOR': '51',
      'MINOR': '0',
      'BUILD': '2704',
      'PATCH': '0',
    },
  )


def GenTests(api: RecipeTestApi):
  yield api.test(
    'basic',
    api.post_process(post_process.DropExpectation),
  )
