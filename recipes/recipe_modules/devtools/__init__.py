# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.build import (
    chromium,
    chromium_swarming,
    v8,
    v8_tests,
)
from RECIPE_MODULES.depot_tools import (
    bot_update,
    gclient,
    git,
    gsutil,
    tryserver,
)
from RECIPE_MODULES.recipe_engine import (
    buildbucket,
    cas,
    context,
    file,
    futures,
    path,
    platform,
    raw_io,
    resultdb,
    step,
    swarming,
    url,
)


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_swarming: chromium_swarming.API
  bot_update: bot_update.API
  gclient: gclient.API
  git: git.API
  gsutil: gsutil.API
  tryserver: tryserver.API
  buildbucket: buildbucket.API
  cas: cas.API
  context: context.API
  file: file.API
  path: path.API
  platform: platform.API
  raw_io: raw_io.API
  resultdb: resultdb.API
  step: step.API
  swarming: swarming.API
  url: url.API
  futures: futures.API
  v8: v8.API
  v8_tests: v8_tests.API

from .api import DevToolsAPI as API
