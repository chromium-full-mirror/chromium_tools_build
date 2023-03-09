# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

DEPS = [
    'chromium',
    'isolate',
    'recipe_engine/platform',
    'recipe_engine/properties',
]


def RunSteps(api):
  api.chromium.set_config('chromium')

  api.isolate.compare_build_artifacts('first_dir', 'second_dir')


def GenTests(api):
  yield api.test(
      'basic',
      api.properties(buildername='test_buildername', buildnumber=123),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'failure',
      api.properties(buildername='test_buildername', buildnumber=123),
      api.step_data('compare_build_artifacts', retcode=1),
      api.post_process(post_process.SummaryMarkdown,
                       "Step('compare_build_artifacts') (retcode: 1)"),
      api.post_process(post_process.StatusFailure),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'win',
      api.platform.name('win'),
      api.properties(buildername='test_buildername', buildnumber=123),
      api.post_check(post_process.StepCommandContains, 'gsutil upload',
                     ('gs://chrome-determinism/test_buildername/123/'
                      'deterministic_build_diffs.tgz')),
      api.post_process(post_process.DropExpectation),
  )
