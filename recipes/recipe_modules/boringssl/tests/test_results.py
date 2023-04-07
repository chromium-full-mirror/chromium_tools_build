# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

DEPS = [
    'boringssl',
    'recipe_engine/step',
]


def RunSteps(api):
  step_result = api.step(
      'run tests', ['./run_test', '-json-output',
                    api.boringssl.test_results()])
  results = step_result.boringssl.test_results
  if results.raw:
    results.as_jsonish()


def GenTests(api):

  yield api.test(
      'default',
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'success',
      api.override_step_data('run tests',
                             api.boringssl.canned_test_output(True)),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'failure',
      api.override_step_data('run tests',
                             api.boringssl.canned_test_output(False)),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )
