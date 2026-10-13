# Copyright (c) 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.recipe_modules.build.pgo import properties

PROPERTIES = properties.InputProperties

from dataclasses import dataclass  # noqa: E402

from recipe_engine.recipe_api import RecipeScriptApi  # noqa: E402
from RECIPE_MODULES.build import (  # noqa: E402
  chromium,
  code_coverage,
  gn,
  profiles,
)
from RECIPE_MODULES.depot_tools import (  # noqa: E402
  git,
  gitiles,
  gsutil,
)
from RECIPE_MODULES.recipe_engine import (  # noqa: E402
  buildbucket,
  context,
  file,
  json,
  path,
  platform,
  properties,
  raw_io,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  code_coverage: code_coverage.API
  git: git.API
  gitiles: gitiles.API
  gsutil: gsutil.API
  gn: gn.API
  profiles: profiles.API
  buildbucket: buildbucket.API
  context: context.API
  file: file.API
  json: json.API
  path: path.API
  platform: platform.API
  properties: properties.API
  raw_io: raw_io.API
  step: step.API


from .api import PgoApi as API  # noqa: E402, F401
from .test_api import PgoTestApi as TEST_API  # noqa: E402, F401
