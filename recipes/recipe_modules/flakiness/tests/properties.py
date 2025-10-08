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
  id_tests = api.flakiness.check_for_flakiness
  api.assertions.assertEqual(id_tests, True)


def GenTests(api):

  yield api.test(
      'basic',
      api.flakiness(check_for_flakiness=True),
      api.post_process(post_process.DropExpectation),
  )
