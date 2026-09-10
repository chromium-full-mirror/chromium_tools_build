# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import MustRun, DropExpectation

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.depot_tools import bot_update, gclient
from RECIPE_MODULES.recipe_engine import path, step


@dataclass
class DEPS(RecipeScriptApi):
  bot_update: bot_update.API
  gclient: gclient.API
  path: path.API
  step: step.API


def RunSteps(api: DEPS):
  api.gclient.set_config('crossbench')
  api.bot_update.ensure_checkout()
  checkout_dir = api.path.start_dir / 'crossbench'

  cmd = [
    'vpython3',
    '-vpython-spec',
    checkout_dir / '.vpython3',
    '-m',
    'pytype',
    '--keep-going',
    '--jobs=auto',
    checkout_dir / 'crossbench',
    '-o',
    api.path.cache_dir / 'pytype',
  ]
  api.step('Run pytype', cmd)


def GenTests(api: RecipeTestApi):
  yield api.test(
    'basic',
    api.post_process(MustRun, 'Run pytype'),
    api.post_process(DropExpectation),
  )
