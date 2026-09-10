# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.recipe_modules.build.angle import properties

PROPERTIES = properties.InputProperties

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.build import (
  chromium,
  chromium_checkout,
  chromium_tests,
  chromium_tests_builder_config,
)
from RECIPE_MODULES.depot_tools import (
  gclient,
  tryserver,
)
from RECIPE_MODULES.recipe_engine import (
  cipd,
  context,
  platform,
  properties,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_checkout: chromium_checkout.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  gclient: gclient.API
  tryserver: tryserver.API
  cipd: cipd.API
  context: context.API
  platform: platform.API
  properties: properties.API
  step: step.API


from .api import ANGLEApi as API
from .test_api import ANGLETestsApi as TEST_API
