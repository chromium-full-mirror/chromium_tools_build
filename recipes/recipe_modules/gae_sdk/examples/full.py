# Copyright 2015 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import gae_sdk
from RECIPE_MODULES.recipe_engine import path, platform, step


@dataclass
class DEPS(RecipeScriptApi):
  gae_sdk: gae_sdk.API
  path: path.API
  platform: platform.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  platform: platform.TEST_API


def RunSteps(api: DEPS):
  api.step('all_packages', [])
  api.step.active_result.presentation.logs['details'] = [
    '%r: %r' % (plat, arch) for plat, arch in api.gae_sdk.all_packages
  ]

  for plat in api.gae_sdk.platforms:
    out = api.gae_sdk.repo_resource(
      'gae_sdk', '%s_%s' % (plat, api.platform.name)
    )
    try:
      api.gae_sdk.fetch(plat, out)
    except api.gae_sdk.PackageNotFound:
      api.step.empty(
        'Failed to fetch',
        status=api.step.FAILURE,
        step_text=(
          'No %s package for %s / %s'
          % (plat, api.platform.name, api.platform.bits)
        ),
      )


def GenTests(api: TEST_DEPS):
  yield api.test(
    'win',
    api.platform('win', 64),
    api.expect_status('FAILURE'),
  )

  yield api.test(
    'linux',
    api.platform('linux', 64),
  )

  yield api.test(
    'mac',
    api.platform('mac', 64),
  )
