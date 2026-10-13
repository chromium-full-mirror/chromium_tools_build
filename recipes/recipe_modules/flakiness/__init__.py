# Copyright (c) 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.recipe_modules.build.flakiness import properties

PROPERTIES = properties.InputProperties

from dataclasses import dataclass  # noqa: E402

from recipe_engine.recipe_api import RecipeScriptApi  # noqa: E402
from RECIPE_MODULES.build import (  # noqa: E402
  chromium,
  chromium_checkout,
  isolate,
  tar,
)
from RECIPE_MODULES.depot_tools import (  # noqa: E402
  bot_update,
  gerrit,
  gsutil,
  tryserver,
)
from RECIPE_MODULES.recipe_engine import (  # noqa: E402
  buildbucket,
  commit_position,
  file,
  futures,
  json,
  led,
  luci_analysis,
  path,
  platform,
  properties,
  resultdb,
  runtime,
  step,
  time,
)


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_checkout: chromium_checkout.API
  bot_update: bot_update.API
  gerrit: gerrit.API
  gsutil: gsutil.API
  tryserver: tryserver.API
  isolate: isolate.API
  buildbucket: buildbucket.API
  commit_position: commit_position.API
  file: file.API
  futures: futures.API
  json: json.API
  led: led.API
  luci_analysis: luci_analysis.API
  path: path.API
  platform: platform.API
  properties: properties.API
  resultdb: resultdb.API
  runtime: runtime.API
  step: step.API
  time: time.API
  tar: tar.API


from .api import FlakinessApi as API  # noqa: E402, F401
from .test_api import FlakinessTestApi as TEST_API  # noqa: E402, F401
