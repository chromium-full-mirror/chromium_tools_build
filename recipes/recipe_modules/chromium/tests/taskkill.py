# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import (DropExpectation, StatusSuccess,
                                        StepSuccess)

DEPS = [
  'chromium',
]


def RunSteps(api):
  api.chromium.taskkill()


def GenTests(api):
  yield api.test(
      'basic',
      api.post_process(StepSuccess, 'taskkill'),
      api.post_process(StatusSuccess),
      api.post_process(DropExpectation),
  )
