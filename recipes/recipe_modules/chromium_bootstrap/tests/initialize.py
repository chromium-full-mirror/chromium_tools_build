# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

DEPS = [
    'chromium_bootstrap',
    'recipe_engine/assertions',
    'recipe_engine/json',
    'recipe_engine/properties',
]


def RunSteps(api):
  # Initialize gets run when the recipe module is loaded
  pass


def GenTests(api):

  yield api.test(
      'not-bootstrapped',
      api.post_check(post_process.DoesNotRun, 'bootstrapped properties'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'bootstrapped',
      api.chromium_bootstrap.properties(commits=[]),
      api.properties(foo='bar'),
      api.post_check(
          post_process.LogEquals,
          'bootstrapped properties',
          'properties',
          api.json.dumps(
              {
                  "$build/chromium_bootstrap": {},
                  "foo": "bar",
                  "recipe": "chromium_bootstrap:tests/initialize"
              },
              indent=2),
      ),
      api.post_process(post_process.DropExpectation),
  )
