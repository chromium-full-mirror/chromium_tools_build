# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from .util import RDBResults  # noqa: F401

from PB.recipe_modules.build.test_utils import properties

PROPERTIES = properties.InputProperties

from dataclasses import dataclass  # noqa: E402

from recipe_engine.recipe_api import RecipeScriptApi  # noqa: E402
from RECIPE_MODULES.build import (  # noqa: E402
  chromium,
  chromium_swarming,
  flakiness,
  presentation_utils,
  repro_instructions,
)
from RECIPE_MODULES.depot_tools import (  # noqa: E402
  depot_tools,
  tryserver,
)
from RECIPE_MODULES.recipe_engine import (  # noqa: E402
  buildbucket,
  futures,
  json,
  legacy_annotation,
  luci_analysis,
  path,
  properties,
  raw_io,
  resultdb,
  step,
  time,
)


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_swarming: chromium_swarming.API
  flakiness: flakiness.API
  depot_tools: depot_tools.API
  tryserver: tryserver.API
  presentation_utils: presentation_utils.API
  buildbucket: buildbucket.API
  futures: futures.API
  json: json.API
  legacy_annotation: legacy_annotation.API
  luci_analysis: luci_analysis.API
  path: path.API
  properties: properties.API
  raw_io: raw_io.API
  resultdb: resultdb.API
  step: step.API
  time: time.API
  repro_instructions: repro_instructions.API


# TODO(phajdan.jr): provide coverage (http://crbug.com/693058).
DISABLE_STRICT_COVERAGE = True


from .api import TestUtilsApi as API  # noqa: E402, F401
from .test_api import TestUtilsTestApi as TEST_API  # noqa: E402, F401
