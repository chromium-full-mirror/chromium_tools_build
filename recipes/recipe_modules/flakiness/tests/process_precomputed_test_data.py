# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

DEPS = [
    'flakiness',
    'recipe_engine/assertions',
]


def RunSteps(api):
  test_data = [{
      'test_id': 'ninja://some/test:module/TestSuite.test_a',
      'variant_hash': 'test_a',
  }]
  tests = api.flakiness.process_precomputed_test_data(test_data)
  api.assertions.assertEqual(len(tests), 1)
  api.assertions.assertTrue(('ninja://some/test:module/TestSuite.test_a',
                             'test_a') in tests)


def GenTests(api):
  yield api.test(
      'basic',
      api.post_process(post_process.DropExpectation),
  )
