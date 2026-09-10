# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import ninjalog


@dataclass
class DEPS(RecipeScriptApi):
  ninjalog: ninjalog.API


def RunSteps(api: DEPS):
  build_step = "compile"
  ninja_command = ['ninja', '-C', 'out/Default', 'chrome']
  build_exit_code = 0
  api.ninjalog.upload(build_step, ninja_command, build_exit_code)


def GenTests(api: RecipeTestApi):
  yield api.test(
    'basic',
    api.post_check(post_process.MustRun, 'gsutil upload ninja_log'),
    api.post_process(post_process.DropExpectation),
  )
