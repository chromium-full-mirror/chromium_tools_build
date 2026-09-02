# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.build import (
    chromium_swarming,
    v8,
    v8_orchestrator,
    v8_tests,
)
from RECIPE_MODULES.depot_tools import (
    bot_update,
    gclient,
    gerrit,
    gsutil,
)
from RECIPE_MODULES.recipe_engine import (
    buildbucket,
    cas,
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
  chromium_swarming: chromium_swarming.API
  bot_update: bot_update.API
  gclient: gclient.API
  gerrit: gerrit.API
  gsutil: gsutil.API
  buildbucket: buildbucket.API
  cas: cas.API
  context: context.API
  file: file.API
  json: json.API
  path: path.API
  raw_io: raw_io.API
  step: step.API
  time: time.API
  v8: v8.API
  v8_orchestrator: v8_orchestrator.API
  v8_tests: v8_tests.API

from .api import V8BuiltinsPgoApi as API
from .test_api import V8BuiltinsPgoTestApi as TEST_API
