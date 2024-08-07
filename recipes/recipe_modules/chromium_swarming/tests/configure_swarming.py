# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process


DEPS = [
    'chromium',
    'chromium_swarming',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/runtime',
]


def RunSteps(api):
  api.chromium.set_config('android', TARGET_PLATFORM='android')

  api.chromium_swarming.configure_swarming(
      precommit=api.properties['precommit'],
      # Fake path to make tests pass.
      path_to_merge_scripts=api.path.start_dir.joinpath('checkout',
                                                        'merge_scripts'))


def GenTests(api):
  yield api.test(
      'precommit_cq',
      api.properties(
          precommit=True,
          patch_project='chromium',
          requester='commit-bot@chromium.org',
          blamelist=['some-user@chromium.org']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'precommit_manual',
      api.properties(precommit=True, patch_project='chromium'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'postcommit',
      api.properties(precommit=False),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'experimental',
      api.properties(precommit=False),
      api.runtime(is_experimental=True),
      api.post_process(post_process.DropExpectation),
  )
