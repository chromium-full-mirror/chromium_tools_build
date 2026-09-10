# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.build import chromium
from RECIPE_MODULES.depot_tools import (
  git,
  tryserver,
)
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  context,
  file,
  json,
  path,
  platform,
  properties,
  raw_io,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  git: git.API
  tryserver: tryserver.API
  buildbucket: buildbucket.API
  context: context.API
  file: file.API
  json: json.API
  path: path.API
  platform: platform.API
  properties: properties.API
  raw_io: raw_io.API
  step: step.API


from .api import FilterApi as API
from .test_api import FilterTestApi as TEST_API
