# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

DEPS = [
  'goma',
  'recipe_engine/buildbucket',
  'recipe_engine/properties',
  'recipe_engine/runtime',
]


def RunSteps(api):
  api.goma.ensure_goma(client_type='candidate')
  api.goma.start()
  api.goma.stop(build_exit_status=0)


def GenTests(api):
  yield api.test(
      'basic',
      api.buildbucket.ci_build(builder='test_buildername'),
      api.post_process(post_process.StepSuccess,
                       'preprocess_for_goma.start_goma'),
      api.post_process(post_process.StepSuccess,
                       'preprocess_for_goma.start cloudtail'),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'luci_and_experimental',
      api.runtime(is_experimental=True),
      api.buildbucket.ci_build(builder='test_buildername'),
      api.post_process(post_process.StepSuccess,
                       'preprocess_for_goma.start_goma'),
      api.post_process(post_process.StepSuccess,
                       'preprocess_for_goma.start cloudtail'),
      api.post_process(post_process.StepCommandContains,
                       'postprocess_for_goma.upload_log', [
                           '--is-experimental',
                       ]),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'failure_during_start_failure_cleanup',
      api.step_data('preprocess_for_goma.start_goma', retcode=1),
      api.step_data(
          'preprocess_for_goma.upload_goma_start_failed_logs', retcode=1),
      api.post_check(post_process.StatusException),
      api.post_check(
          post_process.ResultReason,
          "Infra Failure: Step('preprocess_for_goma.start_goma') (retcode: 1)"),
      api.post_process(post_process.DropExpectation),
  )
