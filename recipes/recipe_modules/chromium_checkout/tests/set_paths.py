# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium_checkout
from RECIPE_MODULES.recipe_engine import assertions, path


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  chromium_checkout: chromium_checkout.API
  path: path.API


def RunSteps(api: DEPS):
  api.chromium_checkout.set_paths(api.path.cleanup_dir, 'fake-repo')
  api.assertions.assertEqual(
    api.chromium_checkout.checkout_dir, api.path.cleanup_dir
  )
  api.assertions.assertEqual(
    api.chromium_checkout.source_dir, api.path.cleanup_dir / 'fake-repo'
  )


def GenTests(api: RecipeTestApi):
  yield api.test(
    'basic',
    api.post_process(post_process.DropExpectation),
  )
