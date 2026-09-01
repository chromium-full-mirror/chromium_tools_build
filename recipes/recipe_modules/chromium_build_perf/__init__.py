# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.build import (
    builder_group,
    chromium,
    reclient,
    siso,
)
from RECIPE_MODULES.depot_tools import gclient
from RECIPE_MODULES.recipe_engine import (
    buildbucket,
    cipd,
    context,
    file,
    json,
    path,
    raw_io,
    step,
    time,
)


@dataclass
class DEPS(RecipeScriptApi):
  builder_group: builder_group.API
  chromium: chromium.API
  gclient: gclient.API
  buildbucket: buildbucket.API
  cipd: cipd.API
  context: context.API
  file: file.API
  json: json.API
  path: path.API
  raw_io: raw_io.API
  step: step.API
  time: time.API
  reclient: reclient.API
  siso: siso.API

from .api import ChromiumBuildPerfApi as API
