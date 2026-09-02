# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium, chromium_swarming
from RECIPE_MODULES.recipe_engine import path, properties, runtime


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_swarming: chromium_swarming.API
  path: path.API
  properties: properties.API
  runtime: runtime.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  properties: properties.TEST_API
  runtime: runtime.TEST_API


def RunSteps(api: DEPS):
  api.chromium.set_config('android', TARGET_PLATFORM='android')

  api.chromium_swarming.configure_swarming(
      precommit=api.properties['precommit'],
      # Fake path to make tests pass.
      path_to_merge_scripts=api.path.start_dir.joinpath('checkout',
                                                        'merge_scripts'))


def GenTests(api: TEST_DEPS):
  yield api.test(
      'precommit_cq',
      api.properties(
          precommit=True,
          patch_project='chromium',
          requester='commit-bot@chromium.org',
          blamelist=['some-user@chromium.org']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'precommit_manual',
      api.properties(precommit=True, patch_project='chromium'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'postcommit',
      api.properties(precommit=False),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'experimental',
      api.properties(precommit=False),
      api.runtime(is_experimental=True),
      api.post_process(post_process.DropExpectation),
  )
