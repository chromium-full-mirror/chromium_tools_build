# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import (DropExpectation, StepException,
                                        StepWarning, SummaryMarkdown)

DEPS = [
    'recipe_engine/path',
    'chromium_android',
]

def RunSteps(api):
  api.path['checkout'] = api.path['cache'] / 'builder' / 'src'
  api.chromium_android.configure_from_properties('base_config')
  api.chromium_android.provision_devices()

def GenTests(api):
  yield api.test(
      'warning_exit_code',
      api.step_data('provision_devices', retcode=88),
      api.post_process(StepWarning, 'provision_devices'),
      api.post_process(SummaryMarkdown,
                       "Warning: Step('provision_devices') (retcode: 88)"),
      api.expect_status('FAILURE'),
      api.post_process(DropExpectation),
  )
  yield api.test(
      'infra_failure_exit_code',
      api.step_data('provision_devices', retcode=87),
      api.post_process(StepException, 'provision_devices'),
      api.post_process(
          SummaryMarkdown,
          "Infra Failure: Step('provision_devices') (retcode: 87)"),
      api.expect_status('INFRA_FAILURE'),
      api.post_process(DropExpectation),
  )
