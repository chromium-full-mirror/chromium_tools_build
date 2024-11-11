# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

DEPS = [
    'chromium',
    'chromium_swarming',
    'chromium_tests',
    'depot_tools/bot_update',
    'profiles',
    'recipe_engine/assertions',
    'recipe_engine/path',
    'recipe_engine/properties',
    'test_utils',
]

from recipe_engine import post_process

from RECIPE_MODULES.build.chromium_tests import steps


def RunSteps(api):
  api.chromium.set_config(
      'chromium',
      TARGET_PLATFORM=api.properties.get('target_platform', 'linux'))

  # Fake path, as the real one depends on having done a chromium checkout.
  checkout_dir = api.path.start_dir
  source_dir = checkout_dir / 'fake-repo'
  build_dir = source_dir / 'out' / 'some_build_dir'
  api.profiles.source_dir = source_dir
  api.chromium_swarming.path_to_merge_scripts = source_dir / 'merge_scripts'
  api.chromium_swarming.set_default_dimension('pool', 'foo')

  test_spec1 = steps.SwarmingGTestTestSpec.create(
      'base_unittests1',
      isolate_profile_data=False,
      server='swarming1.appspot.com',
      dimensions=api.properties.get('dimensions', {'os': 'Linux'}))
  test_spec2 = steps.SwarmingGTestTestSpec.create(
      'base_unittests2',
      isolate_profile_data=False,
      server='swarming2.appspot.com',
      dimensions=api.properties.get('dimensions', {'os': 'Linux'}))

  test1 = test_spec1.get_test(api.chromium_tests)
  test2 = test_spec2.get_test(api.chromium_tests)

  test_options = steps.TestOptions.create()
  test1.test_options = test_options

  with api.assertions.assertRaisesRegexp(
      NotImplementedError,
      'SwarmingGroups across multiple servers not supported.'):
    api.test_utils.run_tests_once(checkout_dir, source_dir, build_dir,
                                  [test1, test2], '')


def GenTests(api):

  yield api.test(
      'basic',
      api.chromium.ci_build(
          builder_group='test_group',
          builder='test_buildername',
      ),
      api.properties(
          swarm_hashes={
              'base_unittests1': 'ffffffffffffffffffffffffffffffffffffffff/111',
              'base_unittests2': 'ffffffffffffffffffffffffffffffffffffffff/111',
          }),
      api.post_process(post_process.DropExpectation),
  )
