# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.recipe_modules.build.pinlist import properties

PROPERTIES = properties.InputProperties

from dataclasses import dataclass  # noqa: E402

from recipe_engine.recipe_api import RecipeScriptApi  # noqa: E402
from RECIPE_MODULES.build import (  # noqa: E402
  chromium,
  chromium_checkout,
)
from RECIPE_MODULES.recipe_engine import (  # noqa: E402
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
  chromium_checkout: chromium_checkout.API
  bcid_reporter: bcid_reporter.API
  buildbucket: buildbucket.API
  cipd: cipd.API
  file: file.API
  led: led.API
  path: path.API
  properties: properties.API
  step: step.API


from .api import PinlistApi as API  # noqa: E402, F401
from .test_api import PinlistTestApi as TEST_API  # noqa: E402, F401
