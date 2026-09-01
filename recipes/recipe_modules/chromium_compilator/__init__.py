from PB.recipe_modules.build.chromium_compilator import properties

PROPERTIES = properties.InputProperties

# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.build import (
    chromium,
    chromium_checkout,
    chromium_rts,
    chromium_swarming,
    chromium_tests,
    chromium_tests_builder_config,
    chromium_turboci,
    code_coverage,
    filter as filter_module,
    flakiness,
    isolate,
    test_utils,
)
from RECIPE_MODULES.depot_tools import tryserver
from RECIPE_MODULES.recipe_engine import (
    buildbucket,
    cas,
    context,
    cv,
    file,
    json,
    luci_analysis,
    path,
    platform,
    properties,
    raw_io,
    resultdb,
    runtime,
    step,
    swarming,
)


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_checkout: chromium_checkout.API
  chromium_rts: chromium_rts.API
  chromium_swarming: chromium_swarming.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  chromium_turboci: chromium_turboci.API
  code_coverage: code_coverage.API
  tryserver: tryserver.API
  filter: filter_module.API
  flakiness: flakiness.API
  isolate: isolate.API
  buildbucket: buildbucket.API
  cas: cas.API
  cv: cv.API
  context: context.API
  file: file.API
  json: json.API
  luci_analysis: luci_analysis.API
  path: path.API
  platform: platform.API
  properties: properties.API
  raw_io: raw_io.API
  resultdb: resultdb.API
  runtime: runtime.API
  step: step.API
  swarming: swarming.API
  test_utils: test_utils.API



from .api import ChromiumCompilatorApi as API
