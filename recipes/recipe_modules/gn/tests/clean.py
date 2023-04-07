# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import DropExpectation, StepCommandContains

DEPS = [
    'recipe_engine/path',
    'gn',
]


def RunSteps(api):
  api.gn.clean(
      api.path['start_dir'].join('out', 'Release'),
      step_name='foobar')


def GenTests(api):
  yield api.test(
      'basic',
      api.post_process(StepCommandContains, 'foobar', [
          'RECIPE_REPO[depot_tools]/gn.py',
          'clean',
          '[START_DIR]/out/Release',
      ]),
      api.post_process(DropExpectation),
  )
