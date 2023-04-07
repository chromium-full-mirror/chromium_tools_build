# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

DEPS = [
    'builder_group',
    'chromium',
    'recipe_engine/assertions',
    'recipe_engine/buildbucket',
    'recipe_engine/properties',
]


def RunSteps(api):
  api.assertions.assertNotEqual(
      len(api.buildbucket.build.input.gerrit_changes), 0)
  api.assertions.assertEqual(api.builder_group.for_current, 'fake-group')


def GenTests(api):
  yield api.test(
      'basic',
      api.chromium.try_build(builder_group='fake-group'),
      api.post_process(post_process.DropExpectation),
  )
