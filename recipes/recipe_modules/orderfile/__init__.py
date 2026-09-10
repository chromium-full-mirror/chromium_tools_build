# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.recipe_modules.build.orderfile import properties

PROPERTIES = properties.InputProperties

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.build import (
  chromium,
  profiles,
)
from RECIPE_MODULES.recipe_engine import (
  bcid_reporter,
  buildbucket,
  cipd,
  file,
  led,
  path,
  properties,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  profiles: profiles.API
  bcid_reporter: bcid_reporter.API
  buildbucket: buildbucket.API
  cipd: cipd.API
  file: file.API
  led: led.API
  path: path.API
  properties: properties.API
  step: step.API


from .api import OrderfileApi as API
from .test_api import OrderfileTestApi as TEST_API
