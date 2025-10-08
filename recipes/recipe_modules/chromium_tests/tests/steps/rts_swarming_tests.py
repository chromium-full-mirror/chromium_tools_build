# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


DEPS = [
    'chromium',
    'chromium_tests',
    'chromium_tests_builder_config',
    'chromium_swarming',
    'profiles',
    'recipe_engine/path',
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
  _, builder_config = (api.chromium_tests_builder_config.lookup_builder())
  api.chromium_tests.configure_build(builder_config)
  update_result, build_dir, _ = (
      api.chromium_tests.prepare_checkout(builder_config))

  checkout_dir = update_result.checkout_dir
  source_dir = update_result.source_root.path
  build_dir = source_dir / 'out' / 'some_build_dir'
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
    test.rts_raw_cmd = ['run', 'test.filter']

    if test.supports_rts:
      test.is_rts = True
      test.pre_run('without patch')
      test.run(checkout_dir, source_dir, build_dir, 'without patch')
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
