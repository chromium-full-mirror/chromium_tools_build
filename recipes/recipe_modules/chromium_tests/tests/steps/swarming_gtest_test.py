# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

DEPS = [
    'chromium',
    'chromium_swarming',
    'chromium_tests',
    'depot_tools/bot_update',
    'isolate',
    'profiles',
    'recipe_engine/assertions',
    'recipe_engine/buildbucket',
    'recipe_engine/commit_position',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/step',
    'recipe_engine/swarming',
    'test_utils',
]

from recipe_engine import post_process

from RECIPE_MODULES.build.chromium_tests import steps


def RunSteps(api):
  api.chromium.set_config(
      'chromium',
      TARGET_PLATFORM=api.properties.get('target_platform', 'linux'))

  # Fake path, as the real one depends on having done a chromium checkout.
  api.profiles.src_dir = api.path['start_dir']
  api.chromium_swarming.path_to_merge_scripts = (
      api.path['cache'].join('merge_scripts'))
  api.chromium_swarming.set_default_dimension('pool', 'foo')
  api.chromium_swarming.set_default_dimension('os', 'Linux')
  api.chromium.set_build_properties({
      'got_webrtc_revision': 'webrtc_sha',
      'got_v8_revision': 'v8_sha',
      'got_revision': 'd3adv3ggie',
      'got_revision_cp': 'refs/heads/main@{#54321}',
  })

  test_spec = steps.SwarmingGTestTestSpec.create(
      'base_unittests',
      override_compile_targets=api.properties.get('override_compile_targets'),
      isolate_coverage_data=api.properties.get('isolate_coverage_data', False))
  test = test_spec.get_test(api.chromium_tests)

  test_options = steps.TestOptions.create()
  test.test_options = test_options

  try:
    assert len(test.get_invocation_names('')) == 0
    api.test_utils.run_tests_once([test], '')
    assert len(test.get_invocation_names('')) > 0
    assert test.runs_on_swarming
  finally:
    api.step('details', [])
    api.step.active_result.presentation.logs['details'] = [
        'compile_targets: %r' % test.compile_targets(),
        'uses_local_devices: %r' % test.uses_local_devices,
        'uses_isolate: %r' % test.uses_isolate,
        'pass_fail_counts: %s' % test.pass_fail_counts(suffix=''),
    ]

    if 'expected_pass_fail_counts' in api.properties:
      api.assertions.assertEqual(
          test.pass_fail_counts(suffix=''),
          api.properties['expected_pass_fail_counts'])


def GenTests(api):

  yield api.test(
      'basic',
      api.chromium.ci_build(
          builder_group='test_group',
          builder='test_buildername',
      ),
      api.properties(swarm_hashes={
          'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/111',
      }),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'android',
      api.chromium.ci_build(
          builder_group='test_group',
          builder='test_buildername',
      ),
      api.properties(
          target_platform='android',
          swarm_hashes={
              'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
          }),
      api.post_process(
          api.swarming.check_triggered_request,
          'test_pre_run.[trigger] base_unittests',
          lambda check, req: check('target_platform:android' in req[0].command),
      ),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'overrides',
      api.chromium.ci_build(builder='test_buildername',),
      api.properties(
          override_compile_targets=['base_unittests_run'],
          builder_group='test_group',
          swarm_hashes={
              'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
          }),
      api.post_process(post_process.LogContains, 'details', 'details',
                       ["compile_targets: 'base_unittests_run'"]),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'no_result_json',
      api.chromium.ci_build(
          builder_group='test_group',
          builder='test_buildername',
      ),
      api.properties(swarm_hashes={
          'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
      }),
      api.override_step_data(
          'base_unittests',
          api.chromium_swarming.canned_summary_output(
              dispatched_task_step_test_data=None, failure=True, retcode=1)),
      api.post_process(post_process.StepFailure, 'base_unittests'),
      api.post_process(
          post_process.LogContains, '$debug - all results',
          'serialized results',
          ['"unexpected_failing_suites": [\n    "base_unittests"']),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'invalid_test_result',
      api.chromium.ci_build(
          builder_group='test_group',
          builder='test_buildername',
      ),
      api.properties(
          swarm_hashes={
              'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
          },
          expected_pass_fail_counts={},
      ),
      api.override_step_data(
          'base_unittests',
          api.chromium_swarming.canned_summary_output(
              api.test_utils.gtest_results(None, 255))),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'isolate_coverage_data',
      api.chromium.ci_build(
          builder_group='test_group',
          builder='test_buildername',
      ),
      api.properties(
          isolate_coverage_data=True,
          swarm_hashes={
              'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
          }),
      api.post_check(
          api.swarming.check_triggered_request,
          'test_pre_run.[trigger] base_unittests', lambda check, req: check(
              req[0].env_vars['LLVM_PROFILE_FILE'] ==
              '${ISOLATED_OUTDIR}/profraw/default-%2m.profraw')),
      api.post_process(post_process.DropExpectation),
  )
