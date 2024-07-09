# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

from RECIPE_MODULES.build.chromium_tests.steps import ResultDB

DEPS = [
    'chromium',
    'chromium_android',
    'chromium_checkout',
    'test_utils',
    'depot_tools/gclient',
    'recipe_engine/buildbucket',
    'recipe_engine/properties',
]


def RunSteps(api):
  api.gclient.set_config('chromium')
  api.gclient.apply_config('android')
  api.chromium.set_config('chromium')
  api.chromium_android.set_config('main_builder', BUILD_CONFIG='Release')
  update_result = api.chromium_checkout.ensure_checkout()
  source_dir = update_result.source_root.path
  build_dir = source_dir / 'out/Release'
  api.chromium_android.run_java_unit_test_suite(
      source_dir,
      build_dir,
      'test_suite',
      target_name=api.properties.get('target_name', 'test_suite'),
      additional_args=api.properties.get('additional_args'),
      json_results_file=api.test_utils.gtest_results())
  api.chromium_android.run_java_unit_test_suite(
      source_dir,
      build_dir,
      'test_suite-with-rdb',
      target_name=api.properties.get('target_name', 'test_suite'),
      additional_args=api.properties.get('additional_args'),
      json_results_file=api.test_utils.gtest_results(),
      resultdb=ResultDB.create(enable=True))


def GenTests(api):
  yield api.test(
      'basic',
      api.buildbucket.try_build(),
      api.override_step_data('test_suite',
                             api.test_utils.canned_gtest_output(passing=True)),
      api.post_process(post_process.MustRun, 'test_suite'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'different-display-name',
      api.buildbucket.try_build(),
      api.properties(target_name='test_target'),
      api.override_step_data('test_suite',
                             api.test_utils.canned_gtest_output(passing=True)),
      api.post_process(post_process.MustRun, 'test_suite'),
      api.post_process(post_process.StepCommandContains, 'test_suite',
                       ['[CACHE]/builder/src/out/Release/bin/run_test_target']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'additional-args',
      api.buildbucket.try_build(),
      api.properties(additional_args=['--foo=bar']),
      api.override_step_data('test_suite',
                             api.test_utils.canned_gtest_output(passing=True)),
      api.post_process(post_process.MustRun, 'test_suite'),
      api.post_process(post_process.StepCommandContains, 'test_suite',
                       ['--foo=bar']),
      api.post_process(post_process.DropExpectation),
  )
