# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.recipe_modules.build.ssci import properties

PROPERTIES = properties.InputProperties

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.build import chromium
from RECIPE_MODULES.depot_tools import (
    depot_tools,
    gsutil,
)
from RECIPE_MODULES.recipe_engine import (
    buildbucket,
    cipd,
    context,
    file,
    futures,
    json,
    path,
    platform,
    properties,
    raw_io,
    step,
    uuid,
)


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  depot_tools: depot_tools.API
  gsutil: gsutil.API
  buildbucket: buildbucket.API
  cipd: cipd.API
  context: context.API
  file: file.API
  futures: futures.API
  json: json.API
  path: path.API
  platform: platform.API
  properties: properties.API
  raw_io: raw_io.API
  step: step.API
  uuid: uuid.API



from .api import SsciAPI as API
