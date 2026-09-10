# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.build import (
  chromium,
  chromium_checkout,
)
from RECIPE_MODULES.depot_tools import (
  bot_update,
  depot_tools,
  gclient,
  gerrit,
  git,
  gitiles,
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
  raw_io,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_checkout: chromium_checkout.API
  bot_update: bot_update.API
  depot_tools: depot_tools.API
  gclient: gclient.API
  tryserver: tryserver.API
  gerrit: gerrit.API
  git: git.API
  gitiles: gitiles.API
  buildbucket: buildbucket.API
  context: context.API
  path: path.API
  platform: platform.API
  properties: properties.API
  step: step.API
  raw_io: raw_io.API
  file: file.API
  json: json.API


from .api import MetadataValidatorApi as API
from .test_api import MetadataValidatorTestApi as TEST_API
