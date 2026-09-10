# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.recipe_modules.build.code_coverage import properties

PROPERTIES = properties.InputProperties

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.build import (
  chromium,
  chromium_checkout,
  gn,
  profiles,
  siso,
)
from RECIPE_MODULES.depot_tools import (
  gclient,
  git,
  gsutil,
  tryserver,
)
from RECIPE_MODULES.recipe_engine import (
  archive,
  buildbucket,
  context,
  file,
  json,
  led,
  path,
  platform,
  properties,
  raw_io,
  service_account,
  step,
  swarming,
)
from RECIPE_MODULES.infra import zip as zip_module


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_checkout: chromium_checkout.API
  gn: gn.API
  gclient: gclient.API
  git: git.API
  gsutil: gsutil.API
  tryserver: tryserver.API
  zip: zip_module.API
  profiles: profiles.API
  siso: siso.API
  archive: archive.API
  buildbucket: buildbucket.API
  context: context.API
  file: file.API
  json: json.API
  led: led.API
  path: path.API
  platform: platform.API
  properties: properties.API
  raw_io: raw_io.API
  service_account: service_account.API
  step: step.API
  swarming: swarming.API


from .api import CodeCoverageApi as API
from .test_api import CodeCoverageTestApi as TEST_API
