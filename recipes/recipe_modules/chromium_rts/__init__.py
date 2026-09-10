# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.build import isolate
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  cipd,
  file,
  futures,
  json,
  path,
  platform,
  raw_io,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  isolate: isolate.API
  buildbucket: buildbucket.API
  cipd: cipd.API
  file: file.API
  futures: futures.API
  json: json.API
  path: path.API
  platform: platform.API
  raw_io: raw_io.API
  step: step.API


from .api import ChromiumRtsApi as API
