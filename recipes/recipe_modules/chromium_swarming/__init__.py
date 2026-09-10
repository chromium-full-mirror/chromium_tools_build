# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from .types import CipdPackage, MergeScript, TriggerScript

from PB.recipe_modules.build.chromium_swarming import (
  properties as properties_pb,
)

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.build import (
  builder_group,
  chromium,
  chromium_checkout,
  code_coverage,
  presentation_utils,
  repro_instructions,
)
from RECIPE_MODULES.depot_tools import tryserver
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  cas,
  cipd,
  context,
  cv,
  json,
  led,
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
  builder_group: builder_group.API
  chromium: chromium.API
  chromium_checkout: chromium_checkout.API
  code_coverage: code_coverage.API
  tryserver: tryserver.API
  presentation_utils: presentation_utils.API
  buildbucket: buildbucket.API
  cas: cas.API
  cipd: cipd.API
  context: context.API
  cv: cv.API
  led: led.API
  json: json.API
  path: path.API
  platform: platform.API
  properties: properties.API
  resultdb: resultdb.API
  raw_io: raw_io.API
  runtime: runtime.API
  step: step.API
  swarming: swarming.API
  repro_instructions: repro_instructions.API


PROPERTIES = properties_pb.InputProperties

# TODO(phajdan.jr): provide coverage (http://crbug.com/693058).
DISABLE_STRICT_COVERAGE = True

from .api import SwarmingTask

from .api import SwarmingApi as API
from .test_api import SwarmingTestApi as TEST_API
