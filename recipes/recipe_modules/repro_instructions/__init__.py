# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  json,
  led,
  path,
  properties,
  resultdb,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  json: json.API
  led: led.API
  path: path.API
  properties: properties.API
  resultdb: resultdb.API
  step: step.API


from .api import ReproInstructionsApi as API
