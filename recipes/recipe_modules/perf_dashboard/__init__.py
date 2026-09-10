# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.build import builder_group
from RECIPE_MODULES.depot_tools import tryserver
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  json,
  platform,
  properties,
  raw_io,
  runtime,
  service_account,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  builder_group: builder_group.API
  tryserver: tryserver.API
  buildbucket: buildbucket.API
  json: json.API
  platform: platform.API
  properties: properties.API
  raw_io: raw_io.API
  runtime: runtime.API
  service_account: service_account.API
  step: step.API


# TODO(phajdan.jr): provide coverage (http://crbug.com/693058).
DISABLE_STRICT_COVERAGE = True

from .api import PerfDashboardApi as API
