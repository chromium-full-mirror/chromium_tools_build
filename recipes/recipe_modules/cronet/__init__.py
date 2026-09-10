# Copyright 2014 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.build import (
  chromium,
  chromium_android,
  perf_dashboard,
)
from RECIPE_MODULES.depot_tools import (
  gclient,
  gsutil,
)
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  json,
  path,
  properties,
  runtime,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_android: chromium_android.API
  gclient: gclient.API
  gsutil: gsutil.API
  perf_dashboard: perf_dashboard.API
  buildbucket: buildbucket.API
  json: json.API
  path: path.API
  properties: properties.API
  runtime: runtime.API
  step: step.API


from .api import CronetApi as API
