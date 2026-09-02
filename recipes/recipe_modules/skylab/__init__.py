# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.build import (
    chromium_checkout,
    test_utils,
)
from RECIPE_MODULES.recipe_engine import (
    buildbucket,
    cipd,
    json,
    path,
    raw_io,
    runtime,
    step,
    swarming,
    time,
)


@dataclass
class DEPS(RecipeScriptApi):
  chromium_checkout: chromium_checkout.API
  buildbucket: buildbucket.API
  cipd: cipd.API
  json: json.API
  raw_io: raw_io.API
  path: path.API
  runtime: runtime.API
  step: step.API
  swarming: swarming.API
  time: time.API
  test_utils: test_utils.API

from .api import SkylabApi as API
from .test_api import SkylabTestApi as TEST_API
