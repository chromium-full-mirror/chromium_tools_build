# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.recipe_modules.build.ytdevinfra import properties

PROPERTIES = properties.InputProperties

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.recipe_engine import (
  path,
  properties,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  path: path.API
  properties: properties.API
  step: step.API


from .api import DevInfraApi as API
