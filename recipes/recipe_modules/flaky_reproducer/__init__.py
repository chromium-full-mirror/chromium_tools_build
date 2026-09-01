# Copyright (c) 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.build import isolate
from RECIPE_MODULES.recipe_engine import (
    buildbucket,
    cas,
    cipd,
    file,
    futures,
    json,
    luci_analysis,
    path,
    raw_io,
    resultdb,
    step,
    swarming,
    url,
)


@dataclass
class DEPS(RecipeScriptApi):
  cas: cas.API
  buildbucket: buildbucket.API
  cipd: cipd.API
  file: file.API
  futures: futures.API
  json: json.API
  luci_analysis: luci_analysis.API
  path: path.API
  raw_io: raw_io.API
  resultdb: resultdb.API
  step: step.API
  swarming: swarming.API
  url: url.API
  isolate: isolate.API

from .api import FlakyReproducer as API
from .test_api import FlakyReproducerTestApi as TEST_API
