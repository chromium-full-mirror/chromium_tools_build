# Copyright 2023 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

DEPS = [
    'chromium',
    'recipe_engine/path',
    'recipe_engine/properties',
    'siso',
]


def RunSteps(api):
  api.chromium.set_config(
      api.properties.get('chromium_config', 'chromium_clang'),
      TARGET_PLATFORM=api.properties.get('target_platform', 'linux'),
      TARGET_CROS_BOARDS=api.properties.get('target_cros_boards'))
  api.path['checkout'] = api.path['cache'] / 'builder' / 'src'
  return api.chromium.cleandead()


def GenTests(api):
  step_name = 'cleandead'
  yield api.test(
      'basic',
      api.post_check(post_process.StepCommandRE, step_name,
                     ['.*/ninja', '-C', '.*', '-t', 'cleandead']),
      api.post_process(post_process.DropExpectation))

  yield api.test(
      'siso', api.siso.properties(),
      api.post_check(post_process.StepCommandRE, step_name,
                     ['.*/siso', 'ninja', '-C', '.*', '-t', 'cleandead']),
      api.post_process(post_process.DropExpectation))
