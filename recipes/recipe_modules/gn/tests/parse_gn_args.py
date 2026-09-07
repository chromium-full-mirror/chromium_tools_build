# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import gn


@dataclass
class DEPS(RecipeScriptApi):
  gn: gn.API

_INPUT_ARGS = ('# some comments\n'
               'target_cpu = "x86"\n'
               'use_remoteexec = true\n')

_EXPECTED_RESULT = {
    'target_cpu': '"x86"',
    'use_remoteexec': 'true',
}


def RunSteps(api: DEPS):
  actual_result = api.gn.parse_gn_args(_INPUT_ARGS)
  assert actual_result == _EXPECTED_RESULT


def GenTests(api: RecipeTestApi):
  yield api.test(
      'basic',
      api.post_process(post_process.DropExpectation),
  )
