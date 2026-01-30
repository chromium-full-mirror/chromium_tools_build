# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


DEPS = [
    'chromium',
    'chromium_android',
    'chromium_tests',
    'depot_tools/bot_update',
    'depot_tools/gclient',
    'recipe_engine/buildbucket',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/resultdb',
    'recipe_engine/step',
    'test_utils',
]

from recipe_engine import post_process

from RECIPE_MODULES.build.chromium_tests import steps


def RunSteps(api):
  api.gclient.set_config('chromium')
  api.chromium.set_config(
      'chromium',
      TARGET_PLATFORM=api.properties.get('target_platform', 'linux'))
  api.chromium_android.set_config('base_config')
  update_result = api.bot_update.ensure_checkout()

  test = steps.LocalGTestTestSpec.create(
      'base_unittests',
      resultdb=steps.ResultDB(result_format='gtest'),
  ).get_test(api.chromium_tests)
  assert not test.runs_on_swarming

  test_options = steps.TestOptions.create(
      test_filter=['foo.bar'], retry_limit=3, run_disabled=True)
  test.test_options = test_options

  checkout_dir = update_result.checkout_dir
  source_dir = update_result.source_root.path
  build_dir = source_dir / 'out' / 'some_build_dir'

  try:
    api.test_utils.run_tests_once(checkout_dir, source_dir, build_dir, [test],
                                  'with patch')
  finally:
    api.step('details', [])
    api.step.active_result.presentation.logs['details'] = [
        'compile_targets: %r' % test.compile_targets(),
        'uses_local_devices: %r' % test.uses_local_devices,
    ]

    api.test_utils.run_tests_once(checkout_dir, source_dir, build_dir, [test],
                                  'without patch')


def GenTests(api):
  yield api.test(
      'basic',
      api.chromium.ci_build(
          builder_group='test_group',
          builder='test_buildername',
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'retry',
      api.chromium.ci_build(
          builder_group='test_group',
          builder='test_buildername',
      ),
      api.override_step_data(
          'base_unittests results',
          stdout=api.raw_io.output_text(
              api.test_utils.rdb_results(
                  'base_unittests', failing_tests=['Test.One']))),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'windows',
      api.chromium.ci_build(
          builder_group='test_group',
          builder='test_buildername',
      ),
      api.platform.name('win'),
      api.properties(target_platform='win',),
      api.post_process(post_process.DropExpectation),
  )
