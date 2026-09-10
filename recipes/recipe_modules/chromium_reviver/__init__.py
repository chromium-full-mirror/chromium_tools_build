# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.build import (
  chromium,
  chromium_polymorphic,
  chromium_swarming,
  chromium_tests,
  code_coverage,
)
from RECIPE_MODULES.recipe_engine import buildbucket


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_polymorphic: chromium_polymorphic.API
  chromium_swarming: chromium_swarming.API
  chromium_tests: chromium_tests.API
  code_coverage: code_coverage.API
  buildbucket: buildbucket.API


from .api import ChromiumReviverApi as API
