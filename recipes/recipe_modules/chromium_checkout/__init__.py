# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.recipe_modules.build.chromium_checkout import properties

PROPERTIES = properties.InputProperties

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.build import (
    chromium,
    chromium_bootstrap,
    repro_instructions,
    siso,
)
from RECIPE_MODULES.depot_tools import (
    bot_update,
    gclient,
    tryserver,
)
from RECIPE_MODULES.recipe_engine import (
    buildbucket,
    context,
    file,
    json,
    path,
    platform,
    properties,
    resultdb,
    runtime,
    step,
)


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_bootstrap: chromium_bootstrap.API
  bot_update: bot_update.API
  gclient: gclient.API
  tryserver: tryserver.API
  buildbucket: buildbucket.API
  context: context.API
  file: file.API
  json: json.API
  path: path.API
  platform: platform.API
  properties: properties.API
  resultdb: resultdb.API
  runtime: runtime.API
  step: step.API
  repro_instructions: repro_instructions.API
  siso: siso.API



from .api import ChromiumCheckoutApi as API
from .test_api import ChromiumCheckoutTestApi as TEST_API
