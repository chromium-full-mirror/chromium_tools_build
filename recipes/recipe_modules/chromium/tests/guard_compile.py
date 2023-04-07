# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

DEPS = [
    'chromium',
    'recipe_engine/path',
]


def RunSteps(api):
  api.chromium.set_config('chromium')
  with api.chromium.guard_compile():
    return api.chromium.compile()


def GenTests(api):
  yield api.test('basic',
                 api.post_check(post_process.MustRun, 'create compile guard'),
                 api.post_check(post_process.MustRun, 'remove compile guard'),
                 api.post_process(post_process.DropExpectation))

  yield api.test('compile-failure',
                 api.override_step_data('compile', retcode=1),
                 api.post_check(post_process.MustRun, 'create compile guard'),
                 api.post_check(post_process.MustRun, 'remove compile guard'),
                 api.expect_status('FAILURE'),
                 api.post_process(post_process.DropExpectation))

  yield api.test(
      'catastrophe', api.override_step_data('compile', retcode=100),
      api.post_check(post_process.MustRun, 'create compile guard'),
      api.post_check(post_process.DoesNotRun, 'remove compile guard'),
      api.expect_status('INFRA_FAILURE'),
      api.post_process(post_process.DropExpectation))

  yield api.test(
      'recovery',
      api.path.exists(api.path['checkout'].join('out', 'Release',
                                                'CR_COMPILE_GUARD.txt')),
      api.post_check(post_process.MustRun, 'remove unreliable output dir'),
      api.post_process(post_process.DropExpectation))
