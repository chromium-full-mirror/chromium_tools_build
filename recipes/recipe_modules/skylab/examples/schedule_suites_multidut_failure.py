# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

DEPS = [
    'chromium_checkout',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/step',
    'skylab',
]

from RECIPE_MODULES.build.chromium_tests.steps import SkylabTestSpec, SkylabTest

from recipe_engine.recipe_api import Property
from recipe_engine import post_process

LACROS_GCS_PATH = 'gs://fake_bucket/lacros.squashfs'


def gen_skylab_test(name, **kwargs):
  t = SkylabTestSpec.create(name, **kwargs).get_test(SkylabTest)
  t.lacros_gcs_path = LACROS_GCS_PATH
  return t


REQUESTS = [
    gen_skylab_test(
        'multi_dut_should_provision_browser_files_len_mismatch',
        cros_board='eve',
        cros_img='eve-release/R88-13545.0.0',
        secondary_cros_board='eve,pixel6',
        secondary_cros_img='eve-release/R88-13545.0.0,',
        should_provision_browser_files=[True, False, True]),
    gen_skylab_test(
        'multi_dut_secondary_cros_img_len_mismatch',
        cros_board='eve',
        cros_img='eve-release/R88-13545.0.0',
        secondary_cros_board='eve,pixel6',
        secondary_cros_img='eve-release/R88-13545.0.0')
]

PROPERTIES = {
    'requests': Property(help="Set of requests", default=[]),
}


def RunSteps(api, requests):
  api.chromium_checkout.set_paths(api.path.cleanup_dir, 'fake-repo')

  with api.step.nest('schedule skylab test'):
    for r in requests:
      api.skylab.schedule_suite(r, '')


def GenTests(api):
  yield api.test(
      'multi_dut_should_provision_browser_files_len_mismatch',
      api.properties(requests=REQUESTS[0:1]),
      api.post_process(post_process.StepFailure,
                       'schedule skylab test.' + REQUESTS[0].name),
      api.post_process(
          post_process.ResultReason,
          'Length of should_provision_browser_files must match secondary_cros_board'
      ),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'multi_dut_secondary_cros_img_len_mismatch',
      api.properties(requests=REQUESTS[1:2]),
      api.post_process(post_process.StepFailure,
                       'schedule skylab test.' + REQUESTS[1].name),
      api.post_process(
          post_process.ResultReason,
          'Length of secondary_cros_img must match secondary_cros_board'),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
  )
