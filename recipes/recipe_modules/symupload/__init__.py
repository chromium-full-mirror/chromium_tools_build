# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.recipe_modules.build.symupload import properties

PROPERTIES = properties.InputProperties

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.build import (
  chromium,
  chromium_checkout,
)
from RECIPE_MODULES.recipe_engine import (
  context,
  file,
  path,
  properties,
  step,
  time,
)
from RECIPE_MODULES.infra import cloudkms


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_checkout: chromium_checkout.API
  cloudkms: cloudkms.API
  context: context.API
  file: file.API
  path: path.API
  properties: properties.API
  step: step.API
  time: time.API


from .api import SymuploadApi as API
from .test_api import SymuploadTestApi as TEST_API
