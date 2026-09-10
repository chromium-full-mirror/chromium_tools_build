# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.


from __future__ import annotations

from recipe_engine.recipe_api import Property
from recipe_engine.config import ConfigGroup, List, Single

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.build import (
  archive,
  builder_group,
  chromium,
  chromiumdash,
  gn,
  isolate,
  perf_dashboard,
  siso,
  test_utils,
  v8_tests,
)
from RECIPE_MODULES.depot_tools import (
  bot_update,
  gclient,
  git,
  gitiles,
  gsutil,
  osx_sdk,
  tryserver,
)
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
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
  time,
  url,
)
from RECIPE_MODULES.infra import docker


@dataclass
class DEPS(RecipeScriptApi):
  archive: archive.API
  builder_group: builder_group.API
  chromium: chromium.API
  chromiumdash: chromiumdash.API
  bot_update: bot_update.API
  gclient: gclient.API
  git: git.API
  gitiles: gitiles.API
  gsutil: gsutil.API
  osx_sdk: osx_sdk.API
  tryserver: tryserver.API
  gn: gn.API
  docker: docker.API
  isolate: isolate.API
  perf_dashboard: perf_dashboard.API
  buildbucket: buildbucket.API
  commit_position: commit_position.API
  context: context.API
  cv: cv.API
  file: file.API
  json: json.API
  led: led.API
  path: path.API
  platform: platform.API
  properties: properties.API
  raw_io: raw_io.API
  runtime: runtime.API
  scheduler: scheduler.API
  step: step.API
  time: time.API
  url: url.API
  siso: siso.API
  test_utils: test_utils.API
  v8_tests: v8_tests.API


PROPERTIES = {
  '$build/v8': Property(
    help='Properties for the v8 module',
    param_name='properties',
    kind=ConfigGroup(
      # Targets to try to isolate even after compilation errors.
      always_isolate_targets=List(str),
      # Whether to use RBE for compilation with the V8 module.
      use_remoteexec=Single(bool),
    ),
    default={},
  ),
}

# TODO(phajdan.jr): provide coverage (http://crbug.com/693058).
DISABLE_STRICT_COVERAGE = True

from .api import V8Api as API
from .test_api import V8TestApi as TEST_API
