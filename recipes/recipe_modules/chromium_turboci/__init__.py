# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.build import chromium
from RECIPE_MODULES.recipe_engine import (
    buildbucket,
    runtime,
    step,
    uuid,
)


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  buildbucket: buildbucket.API
  runtime: runtime.API
  step: step.API
  uuid: uuid.API

from .api import ChromiumTurbociApi as API
