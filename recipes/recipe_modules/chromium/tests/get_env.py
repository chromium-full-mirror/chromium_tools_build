# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine.post_process import DropExpectation, StepEnvContains

DEPS = [
    'chromium',
    'recipe_engine/context',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/step',
]


def RunSteps(api):
  api.chromium.set_config('chromium', TARGET_PLATFORM='mac')

  source_dir = api.path.cache_dir / 'builder/src'

  with api.context(env=api.chromium.get_env(source_dir)):
    api.step('test', ['echo', 'foo'])


def GenTests(api):

  yield api.test(
      'basic',
      api.platform('mac', 64),
      api.post_process(DropExpectation),
  )
