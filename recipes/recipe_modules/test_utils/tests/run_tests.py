# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import attr

from recipe_engine.recipe_api import Property
from recipe_engine import post_process

from RECIPE_MODULES.build.chromium_tests import steps

DEPS = [
    'builder_group',
    'chromium',
    'chromium_checkout',
    'chromium_swarming',
    'chromium_tests',
    'depot_tools/tryserver',
    'recipe_engine/buildbucket',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/resultdb',
    'recipe_engine/step',
    'recipe_engine/swarming',
    'skylab',
    'test_utils',
]

PROPERTIES = {
    'did_complete_first_run': Property(default=True),
    'did_complete_retry_shards': Property(default=True),
    'disable_resultdb': Property(default=False),
    'test_swarming': Property(default=False),
    'test_skylab': Property(default=False),
    'test_experimental': Property(default=False),
    'test_name': Property(default='MockTest'),
    'retry_failed_shards': Property(default=False),
    'retry_invalid_shards': Property(default=False),
}


def RunSteps(api, did_complete_first_run, did_complete_retry_shards,
             disable_resultdb, test_swarming, test_skylab, test_name,
             test_experimental, retry_failed_shards, retry_invalid_shards):
  api.chromium_checkout.set_paths(api.path.cleanup_dir, 'fake-repo')

  api.chromium.set_config('chromium')
  api.chromium.set_build_properties({
      'got_webrtc_revision': 'webrtc_sha',
      'got_v8_revision': 'v8_sha',
      'got_revision': 'd3adv3ggie',
      'got_revision_cp': 'refs/heads/main@{#54321}',
  })
  api.chromium_swarming.path_to_merge_scripts = (
      api.path.cache_dir / 'merge_scripts')
  api.chromium_swarming.set_default_dimension('pool', 'foo')
  api.chromium_swarming.set_default_dimension('os', 'Linux')
  api.chromium.set_build_properties({
      'got_webrtc_revision': 'webrtc_sha',
      'got_v8_revision': 'v8_sha',
      'got_revision': 'd3adv3ggie',
      'got_revision_cp': 'refs/heads/main@{#54321}',
  })

  spec_kwargs = {}
  if disable_resultdb:
    spec_kwargs['resultdb'] = steps.ResultDB.create(enable=False)

  class MockSwarmingTestSpec(steps.SwarmingIsolatedScriptTestSpec):

    @property
    def test_class(self):
      return MockSwarmingTest

  class MockSwarmingTest(steps.SwarmingIsolatedScriptTest):

    def deterministic_failures(self, suffix):
      if self.name.endswith('failed_results') or self.name.endswith(
          'invalid_results'):
        return [self.name]
      return super().deterministic_failures(suffix)

    def has_valid_results(self, suffix):
      if self.name.endswith('invalid_results'):
        return False
      return super().has_valid_results(suffix)

    def did_complete(self, suffix):
      if suffix == '':
        return did_complete_first_run
      return did_complete_retry_shards

  if test_swarming:
    test_specs = [
        MockSwarmingTestSpec.create(
            name=test_name,
            **dict(
                api.properties.get('src_spec', {}).get(test_name, {}),
                **spec_kwargs)),
        MockSwarmingTestSpec.create(
            name=test_name + '_2',
            **dict(
                api.properties.get('src_spec', {}).get(test_name + '_2', {}),
                **spec_kwargs)),
        steps.MockTestSpec.create(name='test3', **spec_kwargs),
        steps.ExperimentalTestSpec.create(
            MockSwarmingTestSpec.create(
                name='disabled_experimental_test', **spec_kwargs),
            experiment_percentage=0),
    ]
  elif test_skylab:
    test_specs = []
    for spec in api.properties['src_spec']:
      common_skylab_kwargs = {
          k: v
          for k, v in spec.items()
          if k not in ['test', 'swarming', 'name']
      }
      common_skylab_kwargs['target_name'] = spec.get('test')
      test_specs.append(
          steps.SkylabTestSpec.create(
              spec.get('name'), **common_skylab_kwargs, **spec_kwargs))
  elif test_experimental:
    test_specs = [
        steps.ExperimentalTestSpec.create(
            MockSwarmingTestSpec.create(
                name='disabled_experimental_test', **spec_kwargs),
            experiment_percentage=0),
        steps.ExperimentalTestSpec.create(
            MockSwarmingTestSpec.create(
                name='enabled_experimental_test', **spec_kwargs),
            experiment_percentage=100)
    ]
  else:
    test_specs = [
        steps.MockTestSpec.create(name=test_name, **spec_kwargs),
        steps.MockTestSpec.create(name='test2', **spec_kwargs)
    ]

  tests = [
      s.get_test(api.chromium_tests)
      for s in test_specs
  ]
  for t in [test for test in tests if test.runs_on_skylab]:
    t.lacros_gcs_path = 'gs://dummy/lacros.zip'
    t.exe_rel_path = 'out/Lacros/chrome'

  checkout_dir = api.path.start_dir
  source_dir = checkout_dir / 'fake-repo'
  build_dir = source_dir / 'out' / 'some_build_dir'
  invalid_suites, failed_tests = api.test_utils.run_tests(
      checkout_dir,
      source_dir,
      build_dir,
      tests,
      '',
      retry_failed_shards=retry_failed_shards,
      retry_invalid_shards=retry_invalid_shards)
  if failed_tests or invalid_suites:
    api.test_utils.record_suite_statuses(tests, '')
    raise api.step.StepFailure(
        'failed: %s' % ' '.join(t.name for t in failed_tests))


def GenTests(api):
  failure_code = steps.MockTest.ExitCodes.FAILURE
  infra_code = steps.MockTest.ExitCodes.INFRA_FAILURE

  yield api.test(
      'success',
      api.chromium.generic_build(
          builder_group='test_group', builder='test_builder'),
      api.properties(test_name='base_unittests'),
      api.post_process(post_process.MustRun, 'test2'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'success_swarming',
      api.chromium.generic_build(
          builder_group='test_group', builder='test_builder'),
      api.properties(
          buildername='test_buildername',
          bot_id='test_bot_id',
          buildnumber=123,
          test_name='base_unittests',
          test_swarming=True,
          swarm_hashes={
              'base_unittests': '[dummy hash for base_unittests/size]',
              'base_unittests_2': '[dummy hash for base_unittests_2/size]',
          }),
      api.chromium_swarming.wait_for_finished_task_set(
          [([], 1), ([['0'], ['1']], 1)], nest_step_name='collect tasks'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'success_swarming_one_task_still_pending',
      api.chromium.generic_build(
          builder_group='test_group', builder='test_builder'),
      api.properties(
          test_name='base_unittests',
          test_swarming=True,
          swarm_hashes={
              'base_unittests': '[dummy hash for base_unittests/size]',
              'base_unittests_2': '[dummy hash for base_unittests_2/size]',
          }),
      api.chromium_swarming.wait_for_finished_task_set(
          [([], 1), ([['1']], 1)], nest_step_name='collect tasks'),
      # There's no call to get_states after there's only one test left pending,
      # as the test_utils logic just calls the regular collect logic on that
      # test.
      api.post_process(post_process.MustRun, 'base_unittests'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'success_swarming_long_pending',
      api.chromium.generic_build(
          builder_group='test_group', builder='test_builder'),
      api.properties(
          test_name='base_unittests',
          test_swarming=True,
          swarm_hashes={
              'base_unittests': '[dummy hash for base_unittests/size]',
              'base_unittests_2': '[dummy hash for base_unittests_2/size]',
          }),
      api.chromium_swarming.wait_for_finished_task_set(
          [([], 1), ([], 1), ([], 1), ([], 1), ([], 1), ([['0'], ['1']], 1)],
          nest_step_name='collect tasks'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'success_skylab_test',
      api.chromium.generic_build(
          builder_group='test_group', builder='test_builder'),
      api.properties(
          src_spec=[{
              'cros_board': 'eve',
              'cros_img': 'eve-release/R89-13631.0.0',
              'name': 'basic_EVE_TOT',
              'test': 'basic',
              'timeout_sec': 3600,
              'autotest_name': 'chromium',
          }],
          test_skylab=True,
      ),
      api.skylab.mock_wait_on_suites('basic_EVE_TOT', 1),
      api.override_step_data(
          'basic_EVE_TOT results',
          stdout=api.raw_io.output_text(
              api.test_utils.rdb_results(
                  'basic_EVE_TOT', passing_tests=['Test.One']))),
      api.post_process(post_process.StepCommandContains,
                       'test_pre_run.basic_EVE_TOT.schedule', [
                           '--board',
                           'eve',
                       ]),
      api.post_process(post_process.StepCommandContains,
                       'test_pre_run.basic_EVE_TOT.schedule', [
                           '--image',
                           'eve-release/R89-13631.0.0',
                       ]),
      api.post_process(post_process.StepCommandContains,
                       'test_pre_run.basic_EVE_TOT.schedule', [
                           '--timeout-mins',
                           '60',
                       ]),
      api.post_process(post_process.StepCommandContains,
                       'test_pre_run.basic_EVE_TOT.schedule',
                       ['--autotest-name', 'chromium']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'failure',
      api.chromium.generic_build(
          builder_group='test_group', builder='test_builder'),
      api.properties(test_name='base_unittests'),
      api.override_step_data('base_unittests', retcode=failure_code),
      api.post_process(post_process.MustRun, 'test2'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'infra_failure',
      api.chromium.generic_build(
          builder_group='test_group', builder='test_builder'),
      api.properties(test_name='base_unittests'),
      api.override_step_data('base_unittests', retcode=infra_code),
      api.post_process(post_process.DoesNotRun, 'test2'),
      api.expect_status('INFRA_FAILURE'),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'retry_invalid_swarming',
      api.chromium.generic_build(
          builder_group='test_group', builder='test_builder'),
      api.properties(
          test_name='base_unittests_invalid_results',
          test_swarming=True,
          retry_invalid_shards=True,
          swarm_hashes={
              'base_unittests_invalid_results':
                  '[dummy hash for base_unittests/size]',
              'base_unittests_invalid_results_2':
                  '[dummy hash for base_unittests_2/size]',
          }),
      api.override_step_data(
          'base_unittests_invalid_results',
          api.chromium_swarming.canned_summary_output(
              api.test_utils.gtest_results('invalid_results', 1),
              failure=True)),
      api.post_process(post_process.MustRun,
                       'base_unittests_invalid_results (retry shards)'),
      api.post_process(post_process.DoesNotRun,
                       'base_unittests_invalid_results_2 (retry shards)'),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
  )
  yield api.test(
      'shards_did_not_complete_then_did_complete_valid_results_exist',
      api.chromium.generic_build(
          builder_group='test_group', builder='test_builder'),
      api.properties(
          did_complete_first_run=False,
          test_name='base_unittests_did_not_complete_then_did_complete',
          test_swarming=True,
          retry_invalid_shards=True,
          swarm_hashes={
              'base_unittests_did_not_complete_then_did_complete':
                  '[dummy hash for base_unittests/size]',
              'base_unittests_did_not_complete_then_did_complete_2':
                  '[dummy hash for base_unittests/size]',
          }),
      api.override_step_data(
          'base_unittests_did_not_complete_then_did_complete',
          api.chromium_swarming.canned_summary_output(
              api.test_utils.gtest_results(
                  'base_unittests_did_not_complete_then_did_complete', 1),
              failure=True)),
      api.override_step_data(
          'base_unittests_did_not_complete_then_did_complete_2',
          api.chromium_swarming.canned_summary_output(
              api.test_utils.gtest_results(
                  'base_unittests_did_not_complete_then_did_complete_2', 1),
              failure=True)),
      api.post_process(
          post_process.MustRun,
          'base_unittests_did_not_complete_then_did_complete (retry shards)'),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'shards_did_not_complete_then_did_not_complete_valid_results_exist',
      api.chromium.generic_build(
          builder_group='test_group', builder='test_builder'),
      api.properties(
          did_complete_first_run=False,
          did_complete_retry_shards=False,
          test_name='base_unittests_did_not_complete_then_did_not_complete',
          test_swarming=True,
          retry_invalid_shards=True,
          swarm_hashes={
              'base_unittests_did_not_complete_then_did_not_complete':
                  '[dummy hash for base_unittests/size]',
              'base_unittests_did_not_complete_then_did_not_complete_2':
                  '[dummy hash for base_unittests/size]',
          }),
      api.override_step_data(
          'base_unittests_did_not_complete_then_did_not_complete',
          api.chromium_swarming.canned_summary_output(
              api.test_utils.gtest_results(
                  'base_unittests_did_not_complete_then_did_not_complete', 1),
              failure=True)),
      api.override_step_data(
          'base_unittests_did_not_complete_then_did_not_complete_2',
          api.chromium_swarming.canned_summary_output(
              api.test_utils.gtest_results(
                  'base_unittests_did_not_complete_then_did_not_complete_2', 1),
              failure=True)),
      api.override_step_data(
          'base_unittests_did_not_complete_then_did_not_complete (retry shards)',
          api.chromium_swarming.canned_summary_output(
              api.test_utils.gtest_results(
                  'base_unittests_did_not_complete_then_did_not_complete (retry shards)',
                  1),
              failure=True)),
      api.post_process(
          post_process.MustRun,
          'base_unittests_did_not_complete_then_did_not_complete (retry shards)'
      ),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'pre_run_failure',
      api.chromium.generic_build(
          builder_group='test_group', builder='test_builder'),
      api.properties(test_name='base_unittests'),
      api.override_step_data(
          'test_pre_run.pre_run base_unittests', retcode=failure_code),
      api.post_process(post_process.MustRun, 'base_unittests'),
      api.post_process(post_process.MustRun, 'test2'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'pre_run_infra_failure',
      api.chromium.generic_build(
          builder_group='test_group', builder='test_builder'),
      api.properties(test_name='base_unittests'),
      api.override_step_data(
          'test_pre_run.pre_run base_unittests', retcode=infra_code),
      api.post_process(post_process.DoesNotRun, 'base_unittests'),
      api.expect_status('INFRA_FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'failure_swarming',
      api.chromium.ci_build(builder='test_builder'),
      api.properties(
          test_name='base_unittests_failed_results',
          test_swarming=True,
          swarm_hashes={
              'base_unittests_failed_results':
                  '[dummy hash for base_unittests/size]',
              'base_unittests_failed_results_2':
                  '[dummy hash for base_unittests_2/size]',
          },
          retry_failed_shards=True,
          retry_invalid_shards=True,
      ),
      api.post_process(post_process.MustRun, 'test3'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'failure_with_resultsdb',
      api.chromium.ci_build(builder='test_builder'),
      api.properties(
          test_name='base_unittests_failed_results',
          test_swarming=True,
          swarm_hashes={
              'base_unittests_failed_results':
                  '[dummy hash for base_unittests/size]',
              'base_unittests_failed_results_2':
                  '[dummy hash for base_unittests_2/size]',
          },
          retry_failed_shards=True,
          retry_invalid_shards=True,
      ),
      api.override_step_data(
          'collect tasks.base_unittests_failed_results results',
          stdout=api.json.invalid(
              api.test_utils.rdb_results(
                  'base_unittests_failed_results',
                  failing_tests=['Test.One']))),
      api.post_process(post_process.MustRun, 'test3'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'tasks_without_invocation',
      api.chromium.ci_build(builder='test_builder'),
      api.properties(
          disable_resultdb=True,
          test_name='base_unittests',
          test_swarming=True,
          swarm_hashes={
              'base_unittests': '[dummy hash for base_unittests/size]',
              'base_unittests_2': '[dummy hash for base_unittests_2/size]',
          },
          retry_failed_shards=True,
          retry_invalid_shards=True,
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'abort_retry_too_many_failures',
      api.chromium.try_build(builder='test_builder'),
      api.properties(
          test_name='base_unittests',
          test_swarming=True,
          swarm_hashes={
              'base_unittests': '[dummy hash for base_unittests/size]',
              'base_unittests_2': '[dummy hash for base_unittests_2/size]',
          },
          retry_failed_shards=True,
          retry_invalid_shards=True,
          **{
              '$build/test_utils': {
                  'min_failed_suites_to_skip_retry': 1,
              },
          }),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests', '', failures=['Test.One']),
      api.post_check(post_process.MustRun, 'abort retry'),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'abort_retry_too_many_failures_for_ci',
      api.chromium.ci_build(builder='test_builder'),
      api.properties(
          test_name='base_unittests',
          test_swarming=True,
          swarm_hashes={
              'base_unittests': '[dummy hash for base_unittests/size]',
              'base_unittests_2': '[dummy hash for base_unittests_2/size]',
          },
          retry_failed_shards=True,
          retry_invalid_shards=True,
          **{
              '$build/test_utils': {
                  'min_failed_suites_to_skip_retry': 1,
              },
          }),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests', '', failures=['Test.One']),
      api.post_check(post_process.MustRun, 'abort retry'),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'experimental_test_failure',
      api.chromium.ci_build(builder='test_builder'),
      api.properties(
          test_experimental=True,
          swarm_hashes={
              'enabled_experimental_test':
                  '[dummy hash for enabled_experimental_test/size]',
          },
          retry_failed_shards=True,
          retry_invalid_shards=True,
      ),
      api.override_step_data(
          'collect tasks.enabled_experimental_test results',
          stdout=api.raw_io.output_text(
              api.test_utils.rdb_results(
                  'enabled_experimental_test', failing_tests=['Test.One']))),
      api.post_check(
          lambda check, steps: check('enabled_experimental_test' in steps[
              'exonerate unrelated test failures'].stdin)),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      # A build with a failed swarming task but only flakily passing test
      # results (eg: an individual test case with results [FAIL, PASS]) should
      # still fail.
      'swarming_failure_but_only_flaky_passing_tests',
      api.chromium.try_build(builder='test_builder'),
      api.properties(
          test_name='base_unittests',
          test_swarming=True,
          swarm_hashes={
              'base_unittests': '[dummy hash for base_unittests/size]',
              'base_unittests_2': '[dummy hash for base_unittests_2/size]',
          }),
      api.override_step_data(
          'base_unittests',
          api.chromium_swarming.canned_summary_output(
              api.json.output({}),
              failure=True,
          )),
      api.override_step_data(
          'collect tasks.base_unittests results',
          stdout=api.raw_io.output_text(
              api.test_utils.rdb_results(
                  'base_unittests', flaky_passing_tests=['Test.One']))),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'limit_many_failures',
      api.chromium.try_build(builder='test_builder'),
      api.properties(
          test_name='base_unittests',
          test_swarming=True,
          swarm_hashes={
              'base_unittests': '[dummy hash for base_unittests/size]',
              'base_unittests_2': '[dummy hash for base_unittests_2/size]',
          },
          retry_failed_shards=True,
          retry_invalid_shards=True,
          **{
              '$build/test_utils': {
                  'max_reported_failures': 10,
              },
          }),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests', '', failures=['test%d' % i for i in range(11)]),
      api.post_process(post_process.StepTextContains, 'base_unittests',
                       ['... 1 more (11 total) ...']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'tests_with_different_servers',
      api.chromium.generic_build(builder='test_builder'),
      api.properties(
          test_name='base_unittests',
          test_swarming=True,
          swarm_hashes={
              'base_unittests': '[dummy hash for base_unittests/size]',
              'base_unittests_2': '[dummy hash for base_unittests_2/size]',
          },
          src_spec={
              'base_unittests': {
                  'server': 'swarming1.com'
              },
              'base_unittests_2': {
                  'server': 'swarming2.com'
              },
          }),
      # Two "wait for tasks" steps, one for each server.
      api.post_check(post_process.MustRun, 'collect tasks.wait for tasks'),
      api.post_check(post_process.MustRun, 'collect tasks.wait for tasks (2)'),
      api.post_check(post_process.MustRun, 'base_unittests'),
      api.post_check(post_process.MustRun, 'base_unittests_2'),
      api.post_process(post_process.DropExpectation),
  )
