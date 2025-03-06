# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import DropExpectation, StepSuccess

DEPS = [
    'chromium',
    'chromium_android',
]


def RunSteps(api):
  api.chromium_android.set_config('main_builder')
  api.chromium.set_config('chromium')
  api.chromium_android.init_and_sync()


def GenTests(api):
  yield api.test(
      'basic',
      api.post_process(StepSuccess, 'cleanup index.lock'),
      api.post_process(StepSuccess, 'bot_update'),
      api.post_process(StepSuccess, 'clean local files'),
      api.post_process(DropExpectation),
  )
