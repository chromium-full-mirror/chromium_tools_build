# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.build import v8
from RECIPE_MODULES.depot_tools import (
  depot_tools,
  gclient,
  gerrit,
  git,
  gitiles,
)
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  cipd,
  context,
  file,
  json,
  path,
  properties,
  raw_io,
  step,
  url,
)
from RECIPE_MODULES.infra import cloudkms


@dataclass
class DEPS(RecipeScriptApi):
  depot_tools: depot_tools.API
  gclient: gclient.API
  gerrit: gerrit.API
  git: git.API
  gitiles: gitiles.API
  cloudkms: cloudkms.API
  buildbucket: buildbucket.API
  cipd: cipd.API
  context: context.API
  file: file.API
  json: json.API
  path: path.API
  properties: properties.API
  raw_io: raw_io.API
  step: step.API
  url: url.API
  v8: v8.API


from .api import V8AutoRoller as API
