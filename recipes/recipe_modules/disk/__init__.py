# Copyright 2015 The Chromium Authors
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

from .api import DiskApi as API
from .test_api import DiskApi as TEST_API
