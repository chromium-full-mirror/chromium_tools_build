# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

from RECIPE_MODULES.build import chromium

DEPS = [
    'chromium',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
]


def RunSteps(api):
  api.chromium.set_config(
      api.properties.get('chromium_config', 'chromium'),
      TARGET_PLATFORM=api.properties.get('target_platform', 'linux'),
      TARGET_CROS_BOARDS=api.properties.get('target_cros_boards'))
  api.path['checkout'] = api.path['cache'] / 'builder' / 'src'

  api.chromium.mb_gen(
      chromium.BuilderId.create_for_group('chromium.perf.pinpoint',
                                          'test-builder'),
      phase='test_phase',
      isolated_targets=['base_unittests_run'],
      android_version_code=3,
      android_version_name='example')


def GenTests(api):

  yield api.test(
      'basic',
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'pinpoint_without_new_conf',
      api.step_data('generate_build_files', retcode=1),
      api.post_process(post_process.StepCommandContains,
                       'generate_build_files (2)', [
                           '-m',
                           'chromium.perf',
                       ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'pinpoint_without_new_conf_failed',
      api.step_data('generate_build_files', retcode=1),
      api.step_data('generate_build_files (2)', retcode=1),
      api.post_process(post_process.StepCommandContains,
                       'generate_build_files (2)', [
                           '-m',
                           'chromium.perf',
                       ]),
      api.post_check(post_process.ResultReason,
                     'Step(\'generate_build_files (2)\') (retcode: 1)'),
      api.post_process(post_process.DropExpectation),
      api.expect_status("FAILURE"),
  )
