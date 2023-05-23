# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

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

  with api.context(env=api.chromium.get_env()):
    api.step('test', ['echo', 'foo'])


def GenTests(api):

  yield api.test(
      'basic',
      api.platform('mac', 64),
      api.post_process(DropExpectation),
  )
