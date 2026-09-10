# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.recipe_modules.build.xcode import properties

PROPERTIES = properties.InputProperties

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.recipe_engine import (
  file,
  json,
  path,
  properties,
)


@dataclass
class DEPS(RecipeScriptApi):
  file: file.API
  path: path.API
  json: json.API
  properties: properties.API


from .api import XcodeApi as API
