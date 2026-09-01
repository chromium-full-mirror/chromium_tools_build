# Copyright 2013 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.build import (
    adb,
    builder_group,
    chromium,
    test_utils,
)
from RECIPE_MODULES.depot_tools import (
    gsutil,
    tryserver,
)
from RECIPE_MODULES.recipe_engine import (
    buildbucket,
    context,
    file,
    json,
    path,
    properties,
    raw_io,
    resultdb,
    step,
)


@dataclass
class DEPS(RecipeScriptApi):
  adb: adb.API
  builder_group: builder_group.API
  chromium: chromium.API
  gsutil: gsutil.API
  tryserver: tryserver.API
  buildbucket: buildbucket.API
  context: context.API
  file: file.API
  json: json.API
  path: path.API
  properties: properties.API
  raw_io: raw_io.API
  resultdb: resultdb.API
  step: step.API
  test_utils: test_utils.API

from .config import config_ctx as CONFIG_CTX

from .api import AndroidApi as API

__all__ = ['CONFIG_CTX', 'API']
