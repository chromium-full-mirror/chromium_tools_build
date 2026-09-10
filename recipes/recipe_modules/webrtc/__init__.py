# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.build import (
  archive,
  builder_group,
  chromium,
  chromium_android,
  chromium_checkout,
  chromium_swarming,
  chromium_tests,
  chromium_tests_builder_config,
  code_coverage,
  filter as filter_module,
  flakiness,
  isolate,
  perf_dashboard,
  profiles,
  siso,
  test_utils,
)
from RECIPE_MODULES.depot_tools import (
  bot_update,
  depot_tools,
  gclient,
  git,
  gitiles,
  gsutil,
  tryserver,
)
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  cas,
  cipd,
  commit_position,
  context,
  cq,
  file,
  json,
  path,
  platform,
  properties,
  raw_io,
  resultdb,
  runtime,
  scheduler,
  service_account,
  step,
)
from RECIPE_MODULES.infra import zip as zip_module


@dataclass
class DEPS(RecipeScriptApi):
  archive: archive.API
  builder_group: builder_group.API
  chromium: chromium.API
  chromium_android: chromium_android.API
  chromium_checkout: chromium_checkout.API
  chromium_swarming: chromium_swarming.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  code_coverage: code_coverage.API
  bot_update: bot_update.API
  depot_tools: depot_tools.API
  gclient: gclient.API
  git: git.API
  gitiles: gitiles.API
  gsutil: gsutil.API
  tryserver: tryserver.API
  zip: zip_module.API
  isolate: isolate.API
  filter: filter_module.API
  flakiness: flakiness.API
  perf_dashboard: perf_dashboard.API
  profiles: profiles.API
  buildbucket: buildbucket.API
  cas: cas.API
  cipd: cipd.API
  commit_position: commit_position.API
  context: context.API
  cq: cq.API
  file: file.API
  json: json.API
  path: path.API
  platform: platform.API
  properties: properties.API
  raw_io: raw_io.API
  resultdb: resultdb.API
  runtime: runtime.API
  scheduler: scheduler.API
  service_account: service_account.API
  step: step.API
  siso: siso.API
  test_utils: test_utils.API


# TODO(phajdan.jr): provide coverage (http://crbug.com/693058).
DISABLE_STRICT_COVERAGE = True

from .api import WebRTCApi as API
from .test_api import WebRTCTestApi as TEST_API
