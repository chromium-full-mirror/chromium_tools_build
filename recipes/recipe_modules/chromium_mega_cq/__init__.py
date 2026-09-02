# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.build import (
    chromium,
    chromium_orchestrator,
)
from RECIPE_MODULES.depot_tools import (
    gerrit,
    gitiles,
    tryserver,
)
from RECIPE_MODULES.recipe_engine import (
    buildbucket,
    cv,
    futures,
    json,
    led,
    raw_io,
    step,
    swarming,
    time,
)


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_orchestrator: chromium_orchestrator.API
  gerrit: gerrit.API
  gitiles: gitiles.API
  tryserver: tryserver.API
  buildbucket: buildbucket.API
  cv: cv.API
  led: led.API
  futures: futures.API
  json: json.API
  raw_io: raw_io.API
  step: step.API
  swarming: swarming.API
  time: time.API

from .api import ChromiumMegaCqApi as API
