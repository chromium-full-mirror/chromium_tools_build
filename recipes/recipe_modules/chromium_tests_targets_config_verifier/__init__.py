# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.build import (
  chromium,
  chromium_tests,
)
from RECIPE_MODULES.depot_tools import (
  bot_update,
  gclient,
  tryserver,
)
from RECIPE_MODULES.recipe_engine import (
  context,
  file,
  json,
  path,
  properties,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_tests: chromium_tests.API
  bot_update: bot_update.API
  gclient: gclient.API
  tryserver: tryserver.API
  context: context.API
  file: file.API
  json: json.API
  path: path.API
  step: step.API
  properties: properties.API


from .api import ChromiumTestsTargetsConfigVerifierApi as API
