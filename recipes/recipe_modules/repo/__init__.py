# Copyright 2013 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.depot_tools import depot_tools
from RECIPE_MODULES.recipe_engine import (
  path,
  raw_io,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  depot_tools: depot_tools.API
  path: path.API
  raw_io: raw_io.API
  step: step.API


from .api import RepoApi as API
