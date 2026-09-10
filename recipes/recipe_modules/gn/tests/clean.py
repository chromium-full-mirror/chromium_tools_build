# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine.post_process import DropExpectation, StepCommandContains

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import gn
from RECIPE_MODULES.recipe_engine import path


@dataclass
class DEPS(RecipeScriptApi):
  gn: gn.API
  path: path.API


def RunSteps(api: DEPS):
  api.gn.clean(
    api.path.cache_dir / 'builder' / 'src' / 'out' / 'Release',
    step_name='foobar',
  )


def GenTests(api: RecipeTestApi):
  yield api.test(
    'basic',
    api.post_process(
      StepCommandContains,
      'foobar',
      [
        'RECIPE_REPO[depot_tools]/gn.py',
        'clean',
        '[CACHE]/builder/src/out/Release',
      ],
    ),
    api.post_process(DropExpectation),
  )
