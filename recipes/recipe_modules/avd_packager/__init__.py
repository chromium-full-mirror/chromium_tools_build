# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.recipe_modules.build.avd_packager import properties

PROPERTIES = properties.InputProperties

from dataclasses import dataclass  # noqa: E402

from recipe_engine.recipe_api import RecipeScriptApi  # noqa: E402
from RECIPE_MODULES.build import chromium_checkout  # noqa: E402
from RECIPE_MODULES.depot_tools import gclient  # noqa: E402
from RECIPE_MODULES.recipe_engine import (  # noqa: E402
  buildbucket,
  cipd,
  context,
  defer,
  json,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  chromium_checkout: chromium_checkout.API
  gclient: gclient.API
  buildbucket: buildbucket.API
  cipd: cipd.API
  context: context.API
  defer: defer.API
  json: json.API
  step: step.API


from .api import AvdPackagerApi as API  # noqa: E402, F401
