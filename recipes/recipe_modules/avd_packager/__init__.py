# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.recipe_modules.build.avd_packager import properties

PROPERTIES = properties.InputProperties

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.build import chromium_checkout
from RECIPE_MODULES.depot_tools import gclient
from RECIPE_MODULES.recipe_engine import (
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


from .api import AvdPackagerApi as API
