# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.recipe_modules.build.chromium_tests_builder_config import properties

PROPERTIES = properties.InputProperties

# Forward symbols for other modules to import
from .builder_config import (  # noqa: E402
  BuildbucketBuilderId,  # noqa: F401
  BuilderConfig,  # noqa: F401
  BuilderConfigException,  # noqa: F401
  delegate_to_builder_spec,  # noqa: F401
)
from .builder_db import BuilderDatabase  # noqa: E402, F401
from .builder_spec import BuilderSpec, COMPILE_AND_TEST, TEST  # noqa: E402, F401
from .try_spec import (  # noqa: E402
  TryDatabase,  # noqa: F401
  TryMirror,  # noqa: F401
  TrySpec,  # noqa: F401
  COMPILE_AND_TEST,  # noqa: F401, F811
  COMPILE,  # noqa: F401
  ALWAYS,  # noqa: F401
  NEVER,  # noqa: F401
)

from dataclasses import dataclass  # noqa: E402

from recipe_engine.recipe_api import RecipeScriptApi  # noqa: E402
from RECIPE_MODULES.build import chromium  # noqa: E402
from RECIPE_MODULES.depot_tools import (  # noqa: E402
  gsutil,
  tryserver,
)
from RECIPE_MODULES.recipe_engine import (  # noqa: E402
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
from .api import ChromiumTestsBuilderConfigApi as API  # noqa: E402, F401
from .test_api import ChromiumTestsBuilderConfigApi as TEST_API  # noqa: E402, F401
