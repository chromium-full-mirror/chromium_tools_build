# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

DEPS = [
    'chromium',
    'chromium_rts',
    'recipe_engine/properties',
]


def RunSteps(api):
  api.chromium_rts.generate_filter_files(None, None, None)


def GenTests(api):
  yield api.test(
      'basic',
      api.chromium.try_build(),
      api.expect_exception('NotImplementedError'),
      api.post_process(post_process.DropExpectation),
  )