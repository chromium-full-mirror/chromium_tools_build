# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import DropExpectation

DEPS = [
  'flakiness',
  'recipe_engine/assertions',
]


def RunSteps(api):
  # test total_duration_milliseconds == 0
  result = api.flakiness._shard_runs(0)
  api.assertions.assertEqual(result, [api.flakiness._repeat_count])


def GenTests(api):
  yield api.test(
    'basic',
    api.post_process(DropExpectation),
  )
