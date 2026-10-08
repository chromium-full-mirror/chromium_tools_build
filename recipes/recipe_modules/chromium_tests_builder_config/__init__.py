# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.recipe_modules.build.chromium_tests_builder_config import properties

PROPERTIES = properties.InputProperties

# Forward symbols for other modules to import
from .builder_config import (
  BuildbucketBuilderId,
  BuilderConfig,
  BuilderConfigException,
  delegate_to_builder_spec,
)
from .builder_db import BuilderDatabase
from .builder_spec import BuilderSpec, COMPILE_AND_TEST, TEST
from .try_spec import (
  TryDatabase,
  TryMirror,
  TrySpec,
  COMPILE_AND_TEST,
  COMPILE,
  ALWAYS,
  NEVER,
)

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.build import chromium
from RECIPE_MODULES.depot_tools import (
  gsutil,
  tryserver,
)
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  platform,
  properties,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  chromium: chromium.API
  gsutil: gsutil.API
  tryserver: tryserver.API
  platform: platform.API
  properties: properties.API
  step: step.API


# These can introduce a circular import, so import them last.
from .api import ChromiumTestsBuilderConfigApi as API
from .test_api import ChromiumTestsBuilderConfigApi as TEST_API
