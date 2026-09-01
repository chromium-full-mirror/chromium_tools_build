# Copyright 2013 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.recipe_modules.build.archive import properties

PROPERTIES = properties.InputProperties

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.build import (
    chromium,
    gn,
    squashfs,
    ssci,
    tar,
)
from RECIPE_MODULES.depot_tools import (
    depot_tools,
    gitiles,
    gsutil,
    tryserver,
)
from RECIPE_MODULES.recipe_engine import (
    bcid_reporter,
    buildbucket,
    cipd,
    commit_position,
    context,
    file,
    json,
    path,
    platform,
    properties,
    runtime,
    step,
    time,
)
from RECIPE_MODULES.infra import zip as zip_module


@dataclass
class DEPS(RecipeScriptApi):
  ssci: ssci.API
  chromium: chromium.API
  depot_tools: depot_tools.API
  gitiles: gitiles.API
  gsutil: gsutil.API
  tryserver: tryserver.API
  gn: gn.API
  zip: zip_module.API
  bcid_reporter: bcid_reporter.API
  buildbucket: buildbucket.API
  cipd: cipd.API
  commit_position: commit_position.API
  context: context.API
  file: file.API
  json: json.API
  path: path.API
  platform: platform.API
  properties: properties.API
  runtime: runtime.API
  step: step.API
  time: time.API
  squashfs: squashfs.API
  tar: tar.API


from .api import ArchiveApi as API
from .test_api import ArchiveApi as TEST_API
