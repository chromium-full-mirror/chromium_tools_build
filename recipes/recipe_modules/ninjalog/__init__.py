# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.depot_tools import gsutil
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  context,
  file,
  json,
  path,
  platform,
  time,
  uuid,
)


@dataclass
class DEPS(RecipeScriptApi):
  gsutil: gsutil.API
  buildbucket: buildbucket.API
  context: context.API
  file: file.API
  json: json.API
  path: path.API
  platform: platform.API
  time: time.API
  uuid: uuid.API


from .api import NinjalogApi as API
