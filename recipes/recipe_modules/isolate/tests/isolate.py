# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process
from recipe_engine.recipe_api import Property

DEPS = [
    'isolate',
    'recipe_engine/path',
]


def RunSteps(api):
  api.isolate.isolate('isolate',
                      api.path['cache'] / 'builder' / 'src' / 'test.isolate')


def GenTests(api):
  yield api.test(
      'basic',
      api.post_process(post_process.DropExpectation),
  )
