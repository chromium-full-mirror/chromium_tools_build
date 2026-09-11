# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from dataclasses import dataclass

from PB.recipe_modules.build.chromium_toolchain import (
  properties as properties_pb,
)
from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  cipd,
  context,
  json,
  properties,
  step,
)

PROPERTIES = properties_pb.InputProperties


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  cipd: cipd.API
  context: context.API
  json: json.API
  properties: properties.API
  step: step.API


from .api import ChromiumToolchainApi as API
from .test_api import ChromiumToolchainTestApi as TEST_API
