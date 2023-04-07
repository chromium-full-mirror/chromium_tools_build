# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

DEPS = [
    'builder_group',
    'chromium',
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

from recipe_engine.recipe_api import Property
from recipe_engine import post_process

from RECIPE_MODULES.build.chromium_tests import steps

from PB.go.chromium.org.luci.buildbucket.proto import build as build_pb2
from PB.go.chromium.org.luci.buildbucket.proto import (builds_service as
                                                       builds_service_pb2)
from PB.go.chromium.org.luci.resultdb.proto.v1 import (invocation as
                                                       rdb_invocation)
from PB.go.chromium.org.luci.resultdb.proto.v1 import (test_result as
                                                       rdb_test_result)
from PB.go.chromium.org.luci.resultdb.proto.v1 import common as rdb_common

PROPERTIES = {
    'abort_on_failure': Property(default=False),
    'test_swarming': Property(default=False),
    'test_skylab': Property(default=False),
    'test_experimental': Property(default=False),
    'test_name': Property(default='MockTest'),
    'retry_failed_shards': Property(default=False),
    'retry_invalid_shards': Property(default=False),
}


def RunSteps(api, test_swarming, test_skylab, test_name, test_experimental,
             abort_on_failure, retry_failed_shards, retry_invalid_shards):
  api.chromium.set_config('chromium')
  api.chromium.set_build_properties({
      'got_webrtc_revision': 'webrtc_sha',
      'got_v8_revision': 'v8_sha',
      'got_revision': 'd3adv3ggie',
      'got_revision_cp': 'refs/heads/main@{#54321}',
  })
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

  class MockSwarmingTestSpec(steps.SwarmingIsolatedScriptTestSpec):

    @property
    def test_class(self):
      return MockSwarmingTest

  class MockSwarmingTest(steps.SwarmingIsolatedScriptTest):

    def deterministic_failures(self, suffix):
      if self.name.endswith('failed_results') or self.name.endswith(
          'invalid_results'):
        return [self]
      return super().deterministic_failures(suffix)

    def has_valid_results(self, suffix):
      if self.name.endswith('invalid_results'):
        return False
      return super().has_valid_results(suffix)

  if test_swarming:
    test_specs = [
        MockSwarmingTestSpec.create(name=test_name),
        MockSwarmingTestSpec.create(name=test_name + '_2'),
        steps.MockTestSpec.create(name='test3'),
        steps.ExperimentalTestSpec.create(
            MockSwarmingTestSpec.create(name='disabled_experimental_test'),
            experiment_percentage=0,
            api=api),
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
          steps.SkylabTestSpec.create(spec.get('name'), **common_skylab_kwargs))
  elif test_experimental:
    test_specs = [
        steps.ExperimentalTestSpec.create(
            MockSwarmingTestSpec.create(name='disabled_experimental_test'),
            experiment_percentage=0,
            api=api),
        steps.ExperimentalTestSpec.create(
            MockSwarmingTestSpec.create(name='enabled_experimental_test'),
            experiment_percentage=100,
            api=api)
    ]
  else:
    test_specs = [
        steps.MockTestSpec.create(
            name=test_name, abort_on_failure=abort_on_failure),
        steps.MockTestSpec.create(name='test2')
    ]

  tests = [
      s.get_test(api.chromium_tests)
      for s in test_specs
      if not s.disabled_reason
  ]
  for t in [test for test in tests if test.is_skylabtest]:
    t.lacros_gcs_path = 'gs://dummy/lacros.zip'
    t.exe_rel_path = 'out/Lacros/chrome'

  _, failed_tests = api.test_utils.run_tests(
      tests,
      '',
      retry_failed_shards=retry_failed_shards,
      retry_invalid_shards=retry_invalid_shards)
  if failed_tests:
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
              'tast_expr': 'lacros.Basic',
              'swarming': {},
              'test': 'basic',
              'timeout_sec': 3600,
              'autotest_name': 'tast.lacros',
          }],
          test_skylab=True,
      ),
      api.skylab.mock_wait_on_suites('find test runner build', 1),
      api.post_process(
          post_process.StepCommandContains,
          'test_pre_run.schedule skylab tests.basic_EVE_TOT.schedule', [
              '-board',
              'eve',
          ]),
      api.post_process(
          post_process.StepCommandContains,
          'test_pre_run.schedule skylab tests.basic_EVE_TOT.schedule', [
              '-image',
              'eve-release/R89-13631.0.0',
          ]),
      api.post_process(
          post_process.StepCommandContains,
          'test_pre_run.schedule skylab tests.basic_EVE_TOT.schedule', [
              '-timeout-mins',
              '60',
          ]),
      api.post_process(
          post_process.StepCommandContains,
          'test_pre_run.schedule skylab tests.basic_EVE_TOT.schedule', [
              'tast.lacros',
          ]),
      api.post_process(
          post_process.StepTextEquals, 'basic_EVE_TOT',
          'Test did not run or failed to report to ResultDB.'
          'Check the CTP build for details.'),
      api.post_process(post_process.SummaryMarkdown, 'failed: basic_EVE_TOT'),
      api.expect_status('FAILURE'),
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
      'failure_abort',
      api.chromium.generic_build(
          builder_group='test_group', builder='test_builder'),
      api.properties(test_name='base_unittests', abort_on_failure=True),
      api.override_step_data('base_unittests', retcode=failure_code),
      api.post_process(post_process.DoesNotRun, 'test2'),
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
      api.chromium_swarming.wait_for_finished_task_set(
          [([], 1), ([['0'], ['1']], 1)], nest_step_name='collect tasks'),
      api.post_process(post_process.MustRun,
                       'base_unittests_invalid_results (retry shards)'),
      api.post_process(post_process.DoesNotRun,
                       'base_unittests_invalid_results_2 (retry shards)'),
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
          test_name='base_unittests',
          test_swarming=True,
          swarm_hashes={
              'base_unittests': '[dummy hash for base_unittests/size]',
              'base_unittests_2': '[dummy hash for base_unittests_2/size]',
          },
          retry_failed_shards=True,
          retry_invalid_shards=True,
      ),
      api.override_step_data(
          'test_pre_run.[trigger] base_unittests',
          api.swarming.trigger(
              ['base_unittests'],
              # Turning off resultdb should remove invocation IDs from the
              # trigger output.
              resultdb=False)),
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
      api.override_step_data(
          'collect tasks.base_unittests results',
          stdout=api.raw_io.output_text(
              api.test_utils.rdb_results(
                  'base_unittests', failing_tests=['Test.One']))),
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
          'enabled_experimental_test results',
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
