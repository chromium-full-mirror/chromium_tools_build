# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

DEPS = [
    'skylab',
]

from RECIPE_MODULES.build.chromium_tests.steps import SkylabTestSpec, SkylabTest

LACROS_GCS_PATH = 'gs://fake_bucket/lacros.squashfs'

name = 'm111_multi_dut_should_provision_browser_files_len_mismatch'
k = dict(
    cros_board='eve',
    cros_img='eve-release/R88-13545.0.0',
    secondary_cros_board='eve,pixel6',
    secondary_cros_img='eve-release/R88-13545.0.0,',
    should_provision_browser_files=[True, False, True],
)
t = SkylabTestSpec.create(name, **k).get_test(SkylabTest)
t.lacros_gcs_path = LACROS_GCS_PATH

REQUESTS = [
    t,
]


def RunSteps(api):
  api.skylab.schedule_suites(REQUESTS)


def GenTests(api):
  yield api.test(
      'multi_dut_should_provision_browser_files_len_mismatch',
      api.post_process(post_process.StepFailure, 'schedule skylab tests'),
      api.post_process(post_process.StepFailure,
                       'schedule skylab tests.' + REQUESTS[0].name),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
  )
