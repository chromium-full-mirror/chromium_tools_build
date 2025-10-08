# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

DEPS = [
    'chromium_bootstrap',
    'recipe_engine/assertions',
    'recipe_engine/properties',
]


def RunSteps(api):
  reasons = api.chromium_bootstrap.skip_analysis_reasons
  expected_reasons = api.properties['expected_reasons']
  api.assertions.assertEqual(reasons, expected_reasons)


def GenTests(api):
  yield api.test(
      'basic',
      api.chromium_bootstrap.properties(skip_analysis_reasons=['foo', 'bar']),
      api.properties(expected_reasons=['foo', 'bar']),
      api.post_process(post_process.DropExpectation),
  )
