# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

DEPS = [
    'chromium',
    'recipe_engine/assertions',
    'recipe_engine/path',
]


# TODO: crbug.com/343511336 - Switch to build_dir and rename file once all
# downstream uses are switched to build_dir
def RunSteps(api):
  api.chromium.output_dir = api.path.checkout_dir
  api.assertions.assertEqual(api.chromium.output_dir, api.path.checkout_dir)


def GenTests(api):
  yield api.test(
      'basic',
      api.post_process(post_process.DropExpectation),
  )
