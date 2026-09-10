# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.depot_tools import (
  gerrit,
  gitiles,
  tryserver,
)
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  json,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  gerrit: gerrit.API
  gitiles: gitiles.API
  tryserver: tryserver.API
  buildbucket: buildbucket.API
  json: json.API
  step: step.API


from .api import ChromiumGerritUitlsApi as API
