# Copyright 2025 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.build import (
    chromium,
    chromium_tests,
    chromium_tests_builder_config,
)
from RECIPE_MODULES.depot_tools import (
    osx_sdk,
    tryserver,
)
from RECIPE_MODULES.recipe_engine import (
    cipd,
    platform,
    step,
)


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  cipd: cipd.API
  osx_sdk: osx_sdk.API
  platform: platform.API
  step: step.API
  tryserver: tryserver.API

from .api import DawnApi as API
from .test_api import DawnTestsApi as TEST_API
