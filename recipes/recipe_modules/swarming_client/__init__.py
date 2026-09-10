# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.depot_tools import git
from RECIPE_MODULES.recipe_engine import (
  path,
  properties,
  raw_io,
  step,
  swarming,
)


@dataclass
class DEPS(RecipeScriptApi):
  git: git.API
  path: path.API
  properties: properties.API
  raw_io: raw_io.API
  step: step.API
  swarming: swarming.API


from .api import SwarmingClientApi as API
