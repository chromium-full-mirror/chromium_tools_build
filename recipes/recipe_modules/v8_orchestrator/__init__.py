# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.depot_tools import tryserver
from RECIPE_MODULES.recipe_engine import (
    buildbucket,
    cipd,
    file,
    json,
    led,
    path,
    properties,
    runtime,
    step,
    swarming,
)


@dataclass
class DEPS(RecipeScriptApi):
  tryserver: tryserver.API
  buildbucket: buildbucket.API
  cipd: cipd.API
  file: file.API
  json: json.API
  led: led.API
  path: path.API
  properties: properties.API
  runtime: runtime.API
  step: step.API
  swarming: swarming.API

from .api import V8OrchestratorApi as API
