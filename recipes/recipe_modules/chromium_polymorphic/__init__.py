# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.recipe_modules.build.chromium_polymorphic import properties

PROPERTIES = properties.InputProperties

from dataclasses import dataclass  # noqa: E402

from recipe_engine.recipe_api import RecipeScriptApi  # noqa: E402
from RECIPE_MODULES.build import chromium_tests_builder_config  # noqa: E402
from RECIPE_MODULES.recipe_engine import (  # noqa: E402
  buildbucket,
  properties,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  chromium_tests_builder_config: chromium_tests_builder_config.API
  buildbucket: buildbucket.API
  properties: properties.API
  step: step.API


from .api import ChromiumPolymorphicApi as API  # noqa: E402, F401
from .test_api import ChromiumPolymorphicTestApi as TEST_API  # noqa: E402, F401
