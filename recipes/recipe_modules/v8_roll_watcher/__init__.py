# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.build import v8
from RECIPE_MODULES.depot_tools import (
  depot_tools,
  gerrit,
  git,
  gsutil,
)
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  change_verifier,
  context,
  file,
  json,
  path,
  properties,
  proto,
  raw_io,
  resultdb,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  depot_tools: depot_tools.API
  gerrit: gerrit.API
  git: git.API
  gsutil: gsutil.API
  buildbucket: buildbucket.API
  change_verifier: change_verifier.API
  context: context.API
  file: file.API
  json: json.API
  path: path.API
  properties: properties.API
  proto: proto.API
  raw_io: raw_io.API
  resultdb: resultdb.API
  step: step.API
  v8: v8.API


from .api import V8RollWatcherApi as API
