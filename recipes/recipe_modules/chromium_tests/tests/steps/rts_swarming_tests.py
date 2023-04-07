# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

DEPS = [
    'chromium',
    'chromium_tests',
    'chromium_tests_builder_config',
    'chromium_swarming',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/step',
]

from recipe_engine import post_process

from RECIPE_MODULES.build.chromium_tests import steps


def RunSteps(api):
  api.chromium.set_config('chromium')
  api.chromium_swarming.set_default_dimension('pool', 'foo')
  api.chromium_swarming.set_default_dimension('os', 'Linux')
  api.chromium.set_build_properties({
      'got_webrtc_revision': 'webrtc_sha',
      'got_v8_revision': 'v8_sha',
      'got_revision': 'd3adv3ggie',
      'got_revision_cp': 'refs/heads/main@{#54321}',
  })
  tests = [
      steps.MockTestSpec.create('base_unittests',
                                supports_rts=True).get_test(api.chromium_tests),
      steps.SwarmingGTestTestSpec.create('base_unittests').get_test(
          api.chromium_tests),
      steps.LocalGTestTestSpec.create('base_unittests').get_test(
          api.chromium_tests),
      steps.SwarmingIsolatedScriptTestSpec.create('isolated_tests').get_test(
          api.chromium_tests),
  ]

  for test in tests:
    test.inverted_raw_cmd = ['run', 'inverted.filter']
    test.rts_raw_cmd = ['run', 'test.filter']

    if test.supports_inverted_rts:
      test.is_inverted_rts = True
      test.pre_run('with patch')
      test.run('with patch')
      assert ('inverted.filter' in test.inverted_raw_cmd)
    if test.supports_rts:
      test.is_inverted_rts = False
      test.is_rts = True
      test.pre_run('without patch')
      test.run('without patch')
      assert ('test.filter' in test.rts_raw_cmd)


def GenTests(api):
  ctbc_api = api.chromium_tests_builder_config
  yield api.test(
      'basic',
      api.platform('linux', 64),
      api.chromium.ci_build(
          builder_group='fake-group',
          builder='fake-tester',
          parent_buildername='fake-builder'),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_tester(
              builder_group='fake-group',
              builder='fake-tester',
          ).with_parent(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(
          swarm_hashes={
              'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
              'isolated_tests': 'ffffffffffffffffffffffffffffffffffffffff/size',
          },),
      api.post_process(post_process.DropExpectation),
  )
