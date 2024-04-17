# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

DEPS = [
    'chromium',
    'chromium_tests',
    'chromium_tests_builder_config',
    'recipe_engine/path',
]


def RunSteps(api):
  api.path.checkout_dir = api.path.cache_dir.join('builder')
  api.chromium.set_config('chromium')
  api.chromium_tests.find_swarming_command_lines('chromium')


def GenTests(api):
  yield api.test(
      'no_build_dir',
      api.chromium.generic_build(
          builder_group='fake-group',
          builder='fake-tester',
      ),
      api.post_process(post_process.DropExpectation),
  )
