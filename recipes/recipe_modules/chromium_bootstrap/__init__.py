# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.infra.chromium import chromium_bootstrap

PROPERTIES = chromium_bootstrap.ChromiumBootstrapModuleProperties

from dataclasses import dataclass  # noqa: E402

from recipe_engine.recipe_api import RecipeScriptApi  # noqa: E402
from RECIPE_MODULES.depot_tools import gclient  # noqa: E402
from RECIPE_MODULES.recipe_engine import (  # noqa: E402
  buildbucket,
  json,
  properties,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  gclient: gclient.API
  json: json.API
  properties: properties.API
  step: step.API


from .api import ChromiumBootstrapApi as API  # noqa: E402, F401
from .test_api import ChromiumBootstrapApi as TEST_API  # noqa: E402, F401
