# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

DEPS = [
    'chromium',
    'recipe_engine/assertions',
    'recipe_engine/path',
]


def RunSteps(api):
  api.chromium.set_config('chromium')

  source_dir = api.path.cleanup_dir

  api.assertions.assertEqual(
      api.chromium.targets_spec_dir(source_dir),
      source_dir / 'testing/buildbot')
  api.assertions.assertEqual(
      api.chromium.analyze_config_path(source_dir),
      source_dir / 'testing/buildbot/trybot_analyze_config.json')


def GenTests(api):
  yield api.test(
      'basic',
      api.post_process(post_process.DropExpectation),
  )
