# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.build import (
  builder_group,
  chromium_swarming,
  isolate,
)
from RECIPE_MODULES.depot_tools import (
  gsutil,
  tryserver,
)
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  cas,
  context,
  json,
  path,
  platform,
  properties,
  raw_io,
  resultdb,
  step,
  swarming,
  time,
)


@dataclass
class DEPS(RecipeScriptApi):
  builder_group: builder_group.API
  chromium_swarming: chromium_swarming.API
  gsutil: gsutil.API
  tryserver: tryserver.API
  isolate: isolate.API
  buildbucket: buildbucket.API
  cas: cas.API
  context: context.API
  json: json.API
  path: path.API
  platform: platform.API
  properties: properties.API
  raw_io: raw_io.API
  resultdb: resultdb.API
  step: step.API
  swarming: swarming.API
  time: time.API


# TODO(http://crbug.com/693058): provide coverage.
DISABLE_STRICT_COVERAGE = True

from .api import V8TestsApi as API
from .test_api import V8TestApi as TEST_API
