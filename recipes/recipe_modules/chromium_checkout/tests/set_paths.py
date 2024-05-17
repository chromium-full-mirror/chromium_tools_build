# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

DEPS = [
    'chromium_checkout',
    'recipe_engine/assertions',
    'recipe_engine/path',
]


def RunSteps(api):
  api.chromium_checkout.set_paths(api.path.cleanup_dir, 'fake-repo')
  api.assertions.assertEqual(api.chromium_checkout.checkout_dir,
                             api.path.cleanup_dir)
  api.assertions.assertEqual(api.chromium_checkout.source_dir,
                             api.path.cleanup_dir / 'fake-repo')


def GenTests(api):
  yield api.test(
      'basic',
      api.post_process(post_process.DropExpectation),
  )
