# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.recipe_modules.build.chromium_utr import request

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.build import (
  chromium,
  chromium_checkout,
  chromium_swarming,
  chromium_tests,
  code_coverage,
  gn,
  isolate,
  profiles,
  siso,
)
from RECIPE_MODULES.depot_tools import (
  gclient,
  git,
)
from RECIPE_MODULES.recipe_engine import (
  context,
  file,
  milo,
  path,
  platform,
  raw_io,
  resultdb,
  step,
  swarming,
)


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_checkout: chromium_checkout.API
  chromium_swarming: chromium_swarming.API
  chromium_tests: chromium_tests.API
  code_coverage: code_coverage.API
  gn: gn.API
  isolate: isolate.API
  profiles: profiles.API
  siso: siso.API
  gclient: gclient.API
  git: git.API
  context: context.API
  file: file.API
  milo: milo.API
  path: path.API
  platform: platform.API
  raw_io: raw_io.API
  resultdb: resultdb.API
  step: step.API
  swarming: swarming.API


ENV_PROPERTIES = request.EnvProperties

from .api import ChromiumUTRApi as API
