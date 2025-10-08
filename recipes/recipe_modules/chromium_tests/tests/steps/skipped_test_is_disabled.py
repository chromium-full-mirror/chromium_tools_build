# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


DEPS = [
    'recipe_engine/assertions',
]

from recipe_engine import post_process

from RECIPE_MODULES.build.chromium_tests.steps import SuccessReuseTest


def RunSteps(api):
  test = SuccessReuseTest(None, None, None)
  api.assertions.assertFalse(test.is_enabled)


def GenTests(api):
  yield api.test(
      'basic',
      api.post_process(post_process.DropExpectation),
  )
