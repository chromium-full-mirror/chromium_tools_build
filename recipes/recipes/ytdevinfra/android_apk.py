# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Recipe to build and test ytdevinfra's standalone APK(s)."""

from recipe_engine.post_process import StepCommandRE, DropExpectation

DEPS = ['recipe_engine/step']


def RunSteps(api):
  api.step('Print recipe title', ['echo', 'Build recipe for Android APK'])


def GenTests(api):
  yield api.test(
      'basic',
      api.post_process(StepCommandRE, 'Print recipe title',
                       ['echo', 'Build recipe for Android APK']),
      api.post_process(DropExpectation),
  )
