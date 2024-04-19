# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import MustRun, DropExpectation

DEPS = [
    'depot_tools/bot_update',
    'depot_tools/gclient',
    'recipe_engine/path',
    'recipe_engine/step',
]


def RunSteps(api):
  api.gclient.set_config('crossbench')
  api.bot_update.ensure_checkout()
  checkout_dir = api.path.start_dir / 'crossbench'

  cmd = [
      'vpython3',
      '-vpython-spec',
      checkout_dir,
      '-m',
      'pytype',
      '--keep-going',
      '--jobs=auto',
      checkout_dir / 'crossbench',
      '-o',
      api.path.cache_dir / 'pytype',
  ]
  api.step('Run pytype', cmd)


def GenTests(api):
  yield api.test(
      'basic',
      api.post_process(MustRun, 'Run pytype'),
      api.post_process(DropExpectation),
  )
