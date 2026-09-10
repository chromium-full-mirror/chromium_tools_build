# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.recipe_modules.build.chromium import properties

PROPERTIES = properties.InputProperties

# Forward symbols for other modules to import
from .config import config_ctx as CONFIG_CTX

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.build import (
  builder_group,
  gn,
  ninjalog,
  repro_instructions,
  siso,
  xcode,
)
from RECIPE_MODULES.depot_tools import (
  bot_update,
  depot_tools,
  gclient,
  git,
  gsutil,
  tryserver,
)
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  cipd,
  commit_position,
  context,
  file,
  json,
  led,
  path,
  platform,
  properties,
  raw_io,
  resultdb,
  runtime,
  step,
  uuid,
)


@dataclass
class DEPS(RecipeScriptApi):
  bot_update: bot_update.API
  depot_tools: depot_tools.API
  gclient: gclient.API
  git: git.API
  gsutil: gsutil.API
  tryserver: tryserver.API
  builder_group: builder_group.API
  gn: gn.API
  ninjalog: ninjalog.API
  buildbucket: buildbucket.API
  cipd: cipd.API
  commit_position: commit_position.API
  context: context.API
  file: file.API
  json: json.API
  led: led.API
  path: path.API
  platform: platform.API
  properties: properties.API
  raw_io: raw_io.API
  resultdb: resultdb.API
  runtime: runtime.API
  step: step.API
  uuid: uuid.API
  repro_instructions: repro_instructions.API
  siso: siso.API
  xcode: xcode.API


# These introduce circular imports, so import them last.
from .api import ChromiumApi as API
from .test_api import ChromiumTestApi as TEST_API

__all__ = ['CONFIG_CTX', 'API', 'TEST_API']
