# Copyright 2015 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.build import (
    chromium,
    chromium_checkout,
    chromium_swarming,
    chromium_tests,
    chromium_tests_builder_config,
    filter as filter_module,
    test_utils,
)
from RECIPE_MODULES.depot_tools import (
    depot_tools,
    gclient,
    git,
)
from RECIPE_MODULES.recipe_engine import (
    buildbucket,
    context,
    json,
    path,
    platform,
    properties,
    raw_io,
    step,
)


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_checkout: chromium_checkout.API
  chromium_swarming: chromium_swarming.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  depot_tools: depot_tools.API
  gclient: gclient.API
  git: git.API
  filter: filter_module.API
  buildbucket: buildbucket.API
  context: context.API
  json: json.API
  path: path.API
  platform: platform.API
  properties: properties.API
  raw_io: raw_io.API
  step: step.API
  test_utils: test_utils.API

from .api import FinditApi as API
