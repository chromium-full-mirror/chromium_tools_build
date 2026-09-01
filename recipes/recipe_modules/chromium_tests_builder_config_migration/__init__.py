# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.recipe_engine import (
    file,
    json,
    path,
    step,
)


@dataclass
class DEPS(RecipeScriptApi):
  file: file.API
  json: json.API
  path: path.API
  step: step.API

# Don't set properties, just let the recipes set the properties to the
# properties proto defined in this module

# Forward symbols that might need to be imported
from .api import BlockerCategory
from .api import ChromiumTestsBuilderConfigMigrationApi as API
