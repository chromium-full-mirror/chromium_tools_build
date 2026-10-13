# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.recipe_modules.build.binary_size import properties

PROPERTIES = properties.InputProperties

from dataclasses import dataclass  # noqa: E402

from recipe_engine.recipe_api import RecipeScriptApi  # noqa: E402
from RECIPE_MODULES.build import (  # noqa: E402
  chromium,
  chromium_android,
  chromium_checkout,
  chromium_tests,
  filter as filter_module,
)
from RECIPE_MODULES.depot_tools import (  # noqa: E402
  bot_update,
  gclient,
  gerrit,
  gitiles,
  gsutil,
  tryserver,
)
from RECIPE_MODULES.recipe_engine import (  # noqa: E402
  buildbucket,
  commit_position,
  context,
  file,
  json,
  path,
  platform,
  properties,
  raw_io,
  step,
  time,
)
from RECIPE_MODULES.infra import zip as zip_module  # noqa: E402


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_android: chromium_android.API
  chromium_checkout: chromium_checkout.API
  chromium_tests: chromium_tests.API
  bot_update: bot_update.API
  gclient: gclient.API
  gerrit: gerrit.API
  gitiles: gitiles.API
  gsutil: gsutil.API
  tryserver: tryserver.API
  filter: filter_module.API
  zip: zip_module.API
  buildbucket: buildbucket.API
  context: context.API
  commit_position: commit_position.API
  file: file.API
  json: json.API
  path: path.API
  platform: platform.API
  properties: properties.API
  raw_io: raw_io.API
  step: step.API
  time: time.API


from .api import BinarySizeApi as API  # noqa: E402, F401
from .test_api import BinarySizeTestApi as TEST_API  # noqa: E402, F401
