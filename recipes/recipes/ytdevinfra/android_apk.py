# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Recipe to build and test ytdevinfra's standalone APK(s)."""

from recipe_engine.post_process import StepCommandRE, DropExpectation

DEPS = [
    'depot_tools/bot_update',
    'depot_tools/gclient',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/step',
    'ytdevinfra',
]


def _checkout_steps(api):
  solution_path = api.path.cache_dir / 'builder'
  api.file.ensure_directory('init cache if not exists', solution_path)

  with api.context(cwd=solution_path):
    api.gclient.set_config('ytdevinfra')
    update_result = api.bot_update.ensure_checkout()
    api.gclient.runhooks()
  return update_result


def RunSteps(api):
  api.step('Print recipe title', ['echo', 'Build recipe for Android APK'])
  api.ytdevinfra.set_config('ytdevinfra_android')
  api.ytdevinfra.title()
  env = {}
  with api.context(env=env):
    _checkout_steps(api)


def GenTests(api):
  yield api.test(
      'basic',
      api.properties(ytdevinfra_recipe_version=0.2),
      api.post_process(StepCommandRE, 'Print title from API module',
                       ['echo', 'Recipe for building']),
      api.post_process(DropExpectation),
  )
  yield api.test(
      'basic-0.1',
      api.properties(ytdevinfra_recipe_version=0.1),
      api.post_process(StepCommandRE, 'Print title from API module',
                       ['echo', 'Recipe for building']),
      api.post_process(DropExpectation),
  )
