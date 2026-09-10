# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.build import (
  chromium,
  siso,
  swarming_client,
)
from RECIPE_MODULES.depot_tools import (
  depot_tools,
  git,
  gsutil,
)
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  cas,
  cipd,
  context,
  file,
  json,
  path,
  platform,
  properties,
  raw_io,
  resultdb,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  depot_tools: depot_tools.API
  git: git.API
  gsutil: gsutil.API
  buildbucket: buildbucket.API
  cas: cas.API
  cipd: cipd.API
  context: context.API
  file: file.API
  json: json.API
  path: path.API
  platform: platform.API
  properties: properties.API
  raw_io: raw_io.API
  resultdb: resultdb.API
  step: step.API
  siso: siso.API
  swarming_client: swarming_client.API


from .api import IsolateApi as API
from .test_api import IsolateTestApi as TEST_API
