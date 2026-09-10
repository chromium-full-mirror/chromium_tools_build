# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Buildbot recipe to run Dawn's CTS roller tool.
It will roll to the latest CTS revision, update expectations to suppress
new failures, and upload a CL for review.
"""

from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import dawn
from RECIPE_MODULES.depot_tools import (
  bot_update,
  depot_tools,
  gclient,
  gsutil,
)
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  context,
  file,
  nodejs,
  path,
  platform,
  step,
  swarming,
)


@dataclass
class DEPS(RecipeScriptApi):
  bot_update: bot_update.API
  buildbucket: buildbucket.API
  context: context.API
  dawn: dawn.API
  depot_tools: depot_tools.API
  file: file.API
  gclient: gclient.API
  gsutil: gsutil.API
  nodejs: nodejs.API
  path: path.API
  platform: platform.API
  step: step.API
  swarming: swarming.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  bot_update: bot_update.TEST_API
  buildbucket: buildbucket.TEST_API
  context: context.TEST_API
  file: file.TEST_API
  gclient: gclient.TEST_API
  path: path.TEST_API
  platform: platform.TEST_API


DAWN_REPO = "https://dawn.googlesource.com/dawn"


def _checkout_steps(api: DEPS):
  solution_path = api.path.cache_dir / 'builder'
  api.file.ensure_directory('init cache if not exists', solution_path)
  with api.context(cwd=solution_path):
    # Checkout dawn and its dependencies (specified in DEPS) using gclient.
    api.gclient.set_config('dawn')
    api.gclient.c.got_revision_mapping['dawn'] = 'got_revision'
    # Standalone developer dawn builds want the dawn checkout in the same
    # directory the .gclient file is in.  Bots want it in a directory called
    # 'dawn'.  To make both cases work, the dawn DEPS file pulls deps and runs
    # hooks relative to the variable "root" which is set to . by default and
    # then to 'dawn' on bots here:
    api.gclient.c.solutions[0].custom_vars = {'dawn_root': 'dawn'}
    update_result = api.bot_update.ensure_checkout()
    api.gclient.runhooks()
  return update_result


NODEJS_VERSION = '16.13.0'


def RunSteps(api: DEPS):
  update_result = _checkout_steps(api)
  source_dir = update_result.source_root.path

  with api.nodejs(NODEJS_VERSION):
    api.step('npm', ['npm', 'version'])

    with api.context(env_prefixes={'PATH': api.dawn.get_go_paths(source_dir)}):
      api.step(
        'Roll WebGPU CTS',
        [
          source_dir.joinpath('tools', 'run'),
          'cts',
          'roll',
          '-verbose',
          '-parent-swarming-run-id',
          api.swarming.task_id,
          '-send-to-gardener',
        ],
      )


def GenTests(api: TEST_DEPS):
  yield api.test(
    'cts-roller',
    api.platform('linux', 64),
    api.buildbucket.ci_build(
      project='dawn', builder='linux', git_repo=DAWN_REPO
    ),
    api.post_process(post_process.DropExpectation),
  )
