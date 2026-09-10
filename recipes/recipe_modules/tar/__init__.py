# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.recipe_engine import (
  json,
  path,
  platform,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  json: json.API
  path: path.API
  platform: platform.API
  step: step.API


from .api import TarApi as API
