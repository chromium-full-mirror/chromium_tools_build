# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.depot_tools import (
  git,
  tryserver,
)
from RECIPE_MODULES.recipe_engine import (
  context,
  file,
  futures,
  json,
  path,
  raw_io,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  git: git.API
  tryserver: tryserver.API
  context: context.API
  file: file.API
  futures: futures.API
  json: json.API
  path: path.API
  raw_io: raw_io.API
  step: step.API


from .api import ChromiumTestsBuilderConfigVerifierApi as API
from .test_api import ChromiumTestsBuilderConfigVerifierApi as TEST_API
