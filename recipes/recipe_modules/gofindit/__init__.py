# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.build import (
  chromium,
  chromium_swarming,
  chromium_tests,
  dawn,
  test_utils,
)
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  json,
  resultdb,
  step,
  swarming,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  chromium: chromium.API
  chromium_swarming: chromium_swarming.API
  chromium_tests: chromium_tests.API
  dawn: dawn.API
  json: json.API
  resultdb: resultdb.API
  step: step.API
  swarming: swarming.API
  test_utils: test_utils.API


from .api import LuciBisectionApi as API
