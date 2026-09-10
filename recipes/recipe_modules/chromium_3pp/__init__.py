# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.recipe_modules.build.chromium_3pp import properties

PROPERTIES = properties.InputProperties

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.build import chromium_checkout
from RECIPE_MODULES.depot_tools import (
  gclient,
  git,
  tryserver,
)
from RECIPE_MODULES.recipe_engine import (
  bcid_reporter,
  context,
  path,
  raw_io,
  runtime,
  step,
)
from RECIPE_MODULES.infra import support_3pp


@dataclass
class DEPS(RecipeScriptApi):
  chromium_checkout: chromium_checkout.API
  gclient: gclient.API
  git: git.API
  tryserver: tryserver.API
  support_3pp: support_3pp.API
  bcid_reporter: bcid_reporter.API
  context: context.API
  path: path.API
  raw_io: raw_io.API
  runtime: runtime.API
  step: step.API


from .api import Chromium3ppApi as API
