# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import (DropExpectation, PropertyEquals,
                                        StepCommandContains)

DEPS = [
    'isolate',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/buildbucket',
]

def RunSteps(api):
  api.path['checkout'] = api.path['cache'] / 'builder' / 'src'
  api.isolate.isolate_tests(
      api.path['checkout'].join('out', 'Release'),
      targets=['dummy_target_1', 'dummy_target_2'])


def GenTests(api):
  yield api.test(
      'basic',
      api.post_process(StepCommandContains, 'isolate tests', [
          '[CACHE]/builder/src/out/Release/dummy_target_1.isolated.gen.json',
          '[CACHE]/builder/src/out/Release/dummy_target_2.isolated.gen.json',
      ]),
      api.post_process(
          PropertyEquals, 'swarm_hashes', {
              'dummy_target_1': '[dummy hash for dummy_target_1/dummy size]',
              'dummy_target_2': '[dummy hash for dummy_target_2/dummy size]'
          }),
      api.post_process(DropExpectation),
  )
