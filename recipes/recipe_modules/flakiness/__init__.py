# Copyright (c) 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.recipe_modules.build.flakiness import properties

PROPERTIES = properties.InputProperties

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.build import (
  chromium,
  chromium_checkout,
  isolate,
  tar,
)
from RECIPE_MODULES.depot_tools import (
  bot_update,
  gerrit,
  gsutil,
  tryserver,
)
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  commit_position,
  file,
  futures,
  json,
  led,
  luci_analysis,
  path,
  platform,
  properties,
  resultdb,
  runtime,
  step,
  time,
)


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_checkout: chromium_checkout.API
  bot_update: bot_update.API
  gerrit: gerrit.API
  gsutil: gsutil.API
  tryserver: tryserver.API
  isolate: isolate.API
  buildbucket: buildbucket.API
  commit_position: commit_position.API
  file: file.API
  futures: futures.API
  json: json.API
  led: led.API
  luci_analysis: luci_analysis.API
  path: path.API
  platform: platform.API
  properties: properties.API
  resultdb: resultdb.API
  runtime: runtime.API
  step: step.API
  time: time.API
  tar: tar.API


from .api import FlakinessApi as API
from .test_api import FlakinessTestApi as TEST_API
