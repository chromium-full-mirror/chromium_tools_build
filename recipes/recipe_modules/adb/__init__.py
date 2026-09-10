# Copyright 2014 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.recipe_engine import (
  json,
  path,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  json: json.API
  path: path.API
  step: step.API


from .api import AdbApi as API
from .test_api import AdbTestApi as TEST_API
