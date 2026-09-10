# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.recipe_modules.build.reclient import properties
from PB.recipe_modules.build.reclient import rbe_metrics_bq

PROPERTIES = properties.InputProperties

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.build import (
  ninjalog,
  siso,
)
from RECIPE_MODULES.depot_tools import (
  gclient,
  gsutil,
)
from RECIPE_MODULES.recipe_engine import (
  archive,
  buildbucket,
  cipd,
  context,
  file,
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
  gclient: gclient.API
  gsutil: gsutil.API
  ninjalog: ninjalog.API
  archive: archive.API
  buildbucket: buildbucket.API
  file: file.API
  cipd: cipd.API
  context: context.API
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
  siso: siso.API


from .api import ReclientApi as API
from .test_api import ReclientTestApi as TEST_API
