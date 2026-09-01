# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.recipe_modules.build.chromium_tests import properties

PROPERTIES = properties.InputProperties

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.build import (
    archive,
    builder_group,
    chromium,
    chromium_android,
    chromium_bootstrap,
    chromium_checkout,
    chromium_rts,
    chromium_swarming,
    chromium_turboci,
    code_coverage,
    filter as filter_module,
    flakiness,
    gn,
    isolate,
    orderfile,
    perf_dashboard,
    pgo,
    pinlist,
    presentation_utils,
    profiles,
    repro_instructions,
    siso,
    skylab,
    ssci,
    symupload,
    test_utils,
)
from RECIPE_MODULES.depot_tools import (
    bot_update,
    depot_tools,
    gclient,
    gerrit,
    gsutil,
    tryserver,
)
from RECIPE_MODULES.recipe_engine import resultdb as engine_resultdb
from RECIPE_MODULES.recipe_engine import (
    bcid_reporter,
    buildbucket,
    cas,
    commit_position,
    context,
    cv,
    file,
    json,
    led,
    path,
    platform,
    properties,
    raw_io,
    runtime,
    scheduler,
    step,
    swarming,
    time,
)
from RECIPE_MODULES.infra import zip as zip_module


@dataclass
class DEPS(RecipeScriptApi):
  archive: archive.API
  builder_group: builder_group.API
  chromium: chromium.API
  chromium_android: chromium_android.API
  chromium_bootstrap: chromium_bootstrap.API
  chromium_checkout: chromium_checkout.API
  chromium_rts: chromium_rts.API
  chromium_swarming: chromium_swarming.API
  chromium_turboci: chromium_turboci.API
  code_coverage: code_coverage.API
  bot_update: bot_update.API
  depot_tools: depot_tools.API
  gerrit: gerrit.API
  gclient: gclient.API
  gsutil: gsutil.API
  tryserver: tryserver.API
  filter: filter_module.API
  flakiness: flakiness.API
  gn: gn.API
  zip: zip_module.API
  isolate: isolate.API
  orderfile: orderfile.API
  perf_dashboard: perf_dashboard.API
  pgo: pgo.API
  pinlist: pinlist.API
  presentation_utils: presentation_utils.API
  profiles: profiles.API
  bcid_reporter: bcid_reporter.API
  buildbucket: buildbucket.API
  cas: cas.API
  commit_position: commit_position.API
  context: context.API
  cv: cv.API
  file: file.API
  led: led.API
  json: json.API
  path: path.API
  platform: platform.API
  properties: properties.API
  raw_io: raw_io.API
  resultdb: engine_resultdb.API
  runtime: runtime.API
  scheduler: scheduler.API
  step: step.API
  swarming: swarming.API
  time: time.API
  repro_instructions: repro_instructions.API
  siso: siso.API
  skylab: skylab.API
  ssci: ssci.API
  symupload: symupload.API
  test_utils: test_utils.API

# Avoid adding a dependency on the chromium_tests_builder_config module, the
# builder config should be self-contained and should be provided to
# chromium_tests; it shouldn't need to lookup any additional builders or access
# the static DBs
assert not hasattr(DEPS, "chromium_tests_builder_config")


# These can introduce a circular dependency, so import them last.
from .api import ChromiumTestsApi as API
from .test_api import ChromiumTestsApi as TEST_API
