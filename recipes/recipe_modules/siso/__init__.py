# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.recipe_modules.build.siso import properties

PROPERTIES = properties.InputProperties



from .api import RUSAGE_FORMAT

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.build import (
    builder_group,
    repro_instructions,
)
from RECIPE_MODULES.depot_tools import (
    gclient,
    gsutil,
    tryserver,
)
from RECIPE_MODULES.recipe_engine import (
    buildbucket,
    cas,
    cipd,
    context,
    futures,
    json,
    led,
    path,
    platform,
    properties,
    raw_io,
    runtime,
    step,
    time,
    uuid,
)


@dataclass
class DEPS(RecipeScriptApi):
  builder_group: builder_group.API
  gclient: gclient.API
  gsutil: gsutil.API
  tryserver: tryserver.API
  buildbucket: buildbucket.API
  cas: cas.API
  cipd: cipd.API
  context: context.API
  futures: futures.API
  json: json.API
  led: led.API
  path: path.API
  platform: platform.API
  properties: properties.API
  raw_io: raw_io.API
  runtime: runtime.API
  step: step.API
  time: time.API
  uuid: uuid.API
  repro_instructions: repro_instructions.API



from .api import SisoApi as API
from .test_api import SisoTestApi as TEST_API
