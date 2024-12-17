# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Buildbot recipe to run Dawn's CTS roller tool.
   It will roll to the latest CTS revision, update expectations to suppress
   new failures, and upload a CL for review.
"""

from recipe_engine import post_process

DEPS = [
    'depot_tools/bot_update',
    'depot_tools/depot_tools',
    'depot_tools/gclient',
    'depot_tools/gsutil',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/nodejs',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/step',
    'recipe_engine/swarming',
]

DAWN_REPO = "https://dawn.googlesource.com/dawn"


def _checkout_steps(api):
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


def RunSteps(api):
  update_result = _checkout_steps(api)
  source_dir = update_result.source_root.path

  with api.nodejs(NODEJS_VERSION):
    api.step('npm', ['npm', 'version'])

    with api.context(
        env_prefixes={'PATH': [source_dir.joinpath('tools', 'golang', 'bin')]}):
      api.step('Roll WebGPU CTS', [
          source_dir.joinpath('tools', 'run'),
          'cts',
          'roll',
          '-verbose',
          '-parent-swarming-run-id',
          api.swarming.task_id,
          '-send-to-gardener',
      ])


def GenTests(api):
  yield api.test(
      'cts-roller',
      api.platform('linux', 64),
      api.buildbucket.ci_build(
          project='dawn', builder='linux', git_repo=DAWN_REPO),
      api.post_process(post_process.DropExpectation),
  )
