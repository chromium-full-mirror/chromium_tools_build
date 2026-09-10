# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Recipe to build and test ytdevinfra's standalone APK(s)."""

from recipe_engine.post_process import StepCommandRE, DropExpectation

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import ytdevinfra
from RECIPE_MODULES.depot_tools import bot_update, gclient
from RECIPE_MODULES.recipe_engine import (
  context,
  file,
  path,
  platform,
  properties,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  bot_update: bot_update.API
  context: context.API
  file: file.API
  gclient: gclient.API
  path: path.API
  platform: platform.API
  properties: properties.API
  step: step.API
  ytdevinfra: ytdevinfra.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  bot_update: bot_update.TEST_API
  context: context.TEST_API
  file: file.TEST_API
  gclient: gclient.TEST_API
  path: path.TEST_API
  platform: platform.TEST_API
  properties: properties.TEST_API


def _checkout_steps(api: DEPS):
  solution_path = api.path.cache_dir / 'builder'
  api.file.ensure_directory('init cache if not exists', solution_path)

  with api.context(cwd=solution_path):
    api.gclient.set_config('ytdevinfra')
    update_result = api.bot_update.ensure_checkout()
    api.gclient.runhooks()
  return update_result


def RunSteps(api: DEPS):
  api.step('Print recipe title', ['echo', 'Build recipe for Android APK'])
  api.ytdevinfra.set_config('ytdevinfra_android')
  api.ytdevinfra.title()

  # Run shell scripts with bash on Windows
  shell_wrapper = ('bash', '--') if api.platform.is_win else ()

  env = {}
  with api.context(env=env):
    update_result = _checkout_steps(api)
    source_dir = update_result.source_root.path

    if api.platform.is_linux:
      with api.context(cwd=source_dir.joinpath('chromium', 'src')):
        api.step(
          'Generate build files (1)',
          ['cobalt/build/gn.py', '-p', 'linux-x64x11', '-c', 'devel'],
          wrapper=shell_wrapper,
        )

        api.step(
          'Generate build files (2)',
          [
            'gn',
            'args',
            './out/linux-x64x11_devel',
            '--list=is_component_build',
          ],
          wrapper=shell_wrapper,
        )

        api.step(
          'Enable pre-commit (1)',
          ['pre-commit', 'clean'],
          wrapper=shell_wrapper,
        )

        api.step(
          'Enable pre-commit (2)',
          ['pre-commit', 'install', '-t', 'pre-commit', '-t', 'pre-push'],
          wrapper=shell_wrapper,
        )

        api.step(
          'Build',
          [
            'time',
            'autoninja',
            '-C',
            'out/linux-x64x11_devel',
            'cobalt:gn_all content_shell',
          ],
          wrapper=shell_wrapper,
        )


def GenTests(api: TEST_DEPS):
  yield api.test(
    'basic',
    api.properties(ytdevinfra_recipe_version=0.2),
    api.post_process(
      StepCommandRE,
      'Print title from API module',
      ['echo', 'Recipe for building'],
    ),
    api.post_process(DropExpectation),
  ) + api.platform('linux', 64)
  yield api.test(
    'basic-0.1',
    api.properties(ytdevinfra_recipe_version=0.1),
    api.post_process(
      StepCommandRE,
      'Print title from API module',
      ['echo', 'Recipe for building'],
    ),
    api.post_process(DropExpectation),
  ) + api.platform('linux', 64)
