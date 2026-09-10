# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.recipe_modules.build.chromium_orchestrator import properties

PROPERTIES = properties.InputProperties

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.build import (
  builder_group,
  chromium,
  chromium_bootstrap,
  chromium_checkout,
  chromium_rts,
  chromium_swarming,
  chromium_tests,
  chromium_tests_builder_config,
  code_coverage,
  flakiness,
  isolate,
  profiles,
  repro_instructions,
  test_utils,
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
  cv,
  file,
  futures,
  json,
  led,
  path,
  properties,
  raw_io,
  runtime,
  step,
  swarming,
)


@dataclass
class DEPS(RecipeScriptApi):
  builder_group: builder_group.API
  chromium: chromium.API
  chromium_bootstrap: chromium_bootstrap.API
  chromium_checkout: chromium_checkout.API
  chromium_rts: chromium_rts.API
  chromium_swarming: chromium_swarming.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  code_coverage: code_coverage.API
  gclient: gclient.API
  gsutil: gsutil.API
  tryserver: tryserver.API
  flakiness: flakiness.API
  isolate: isolate.API
  profiles: profiles.API
  buildbucket: buildbucket.API
  cas: cas.API
  cv: cv.API
  cipd: cipd.API
  file: file.API
  futures: futures.API
  json: json.API
  led: led.API
  path: path.API
  properties: properties.API
  raw_io: raw_io.API
  runtime: runtime.API
  step: step.API
  swarming: swarming.API
  repro_instructions: repro_instructions.API
  test_utils: test_utils.API


from .api import ChromiumOrchestratorApi as API
from .test_api import ChromiumOrchestratorApi as TEST_API
