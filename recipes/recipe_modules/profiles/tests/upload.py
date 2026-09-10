# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium_checkout, profiles
from RECIPE_MODULES.recipe_engine import assertions, path


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  chromium_checkout: chromium_checkout.API
  path: path.API
  profiles: profiles.API


def RunSteps(api: DEPS):
  # fake path for start_dir
  api.profiles.upload(
    'bucket',
    'path/artifact.txt',
    '/local/tmp/artifact.txt',
    args=['-Z'],
    link_name='artifact.txt',
  )


def GenTests(api: RecipeTestApi):

  yield api.test(
    'basic',
    api.post_process(post_process.MustRun, 'gsutil upload artifact to GS'),
    api.post_process(post_process.DropExpectation),
  )
