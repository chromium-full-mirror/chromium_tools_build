# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import attr
import re

from google.protobuf import json_format

from recipe_engine import post_process
from recipe_engine.recipe_api import Property

from PB.recipe_engine import result as result_pb2
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc
from RECIPE_MODULES.build.chromium_tests import steps

DEPS = [
    'chromium',
    'chromium_swarming',
    'chromium_tests',
    'chromium_tests_builder_config',
    'depot_tools/tryserver',
    'recipe_engine/assertions',
    'recipe_engine/json',
    'recipe_engine/luci_analysis',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/resultdb',
    'recipe_engine/step',
    'recipe_engine/swarming',
    'test_utils',
]

PROPERTIES = {
    'fail_calculate_tests': Property(default=False, kind=bool),
    'fail_mb_and_compile': Property(default=False, kind=bool),
    'expected_jsonish_result': Property(default=None),
}


def RunSteps(api, fail_calculate_tests, fail_mb_and_compile,
             expected_jsonish_result):
  assert api.tryserver.is_tryserver
  _, builder_config = api.chromium_tests_builder_config.lookup_builder()

  api.chromium_tests.configure_build(builder_config)
  api.chromium_swarming.configure_swarming(
      'chromium',
      precommit=True,
      # Fake path to make tests pass.
      path_to_merge_scripts=api.path['start_dir'].join('checkout',
                                                       'merge_scripts'))

  update_step, _ = api.chromium_tests.prepare_checkout(builder_config)

  kwargs = {}
  if api.properties.get('shards'):
    kwargs['shards'] = api.properties['shards']

  test_specs = [steps.SwarmingGTestTestSpec.create('base_unittests', **kwargs)]

  if api.properties.get('use_custom_dimensions', False):
    api.chromium_swarming.set_default_dimension('os', 'Windows-11-19045')
  else:
    api.chromium_swarming.set_default_dimension('os', 'Linux')

  affected_files = api.properties.get('affected_files', [])

  retry_failed_shards = api.properties.get('retry_failed_shards', False)

  # Allows testing the scenario where there are multiple test suites.
  for t in api.properties.get('additional_gtest_targets', []):
    test_specs.append(steps.SwarmingGTestTestSpec.create(t))

  tests = [s.get_test(api.chromium_tests) for s in test_specs]

  # Override build_affected_targets to run the desired test, in the desired
  # configuration.
  def config_override(builder_id, builder_config, **kwargs):
    builder_config = attr.evolve(
        builder_config, retry_failed_shards=retry_failed_shards)
    task = api.chromium_tests.Task(builder_config, tests, update_step,
                                   affected_files)
    raw_result = result_pb2.RawResult(status=common_pb.SUCCESS)
    if fail_calculate_tests:
      raw_result.summary_markdown = (
          'Compile step failed from "build_affected_targets".')
      raw_result.status = common_pb.FAILURE
    return raw_result, task

  api.chromium_tests.build_affected_targets = config_override

  def compile_override(*args, **kwargs):
    return result_pb2.RawResult(
        status=common_pb.FAILURE,
        summary_markdown='Compile step failed from "run_mb_and_compile".'
    )

  if fail_mb_and_compile:
    api.chromium_tests.run_mb_and_compile = compile_override

  builder_id, builder_config = (
      api.chromium_tests_builder_config.lookup_builder())
  result = api.chromium_tests.trybot_steps(builder_id, builder_config)
  if expected_jsonish_result is not None:
    api.assertions.assertDictEqual(
        expected_jsonish_result,
        api.json.loads(json_format.MessageToJson(result)))

  return result


def GenTests(api):
  ctbc_api = api.chromium_tests_builder_config

  yield api.test(
      'basic',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(swarm_hashes={
          'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
      }),
      api.override_step_data(
          'base_unittests (with patch)',
          api.chromium_swarming.canned_summary_output(
              api.test_utils.canned_gtest_output(False), failure=True)),
      api.post_process(post_process.SummaryMarkdown,
                       '1 Test Suite(s) failed.\n\n**base_unittests** failed.'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'calculate_tests_compile_failure',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(fail_calculate_tests=True),
      api.expect_status('FAILURE'),
      api.post_process(post_process.ResultReason,
                       'Compile step failed from "build_affected_targets".'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'run_mb_and_compile_failure',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(
          fail_mb_and_compile=True,
          swarm_hashes={
              'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
          }),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests', 'with patch', failures=['Test.One']),
      api.expect_status('FAILURE'),
      api.post_process(post_process.ResultReason,
                       'Compile step failed from "run_mb_and_compile".'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'test_failures_prevent_cq_retry',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(
          retry_failed_shards=True,
          swarm_hashes={
              'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
          }),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests', 'with patch', failures=['Test.One']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests', 'retry shards with patch', failures=['Test.One']),
      api.post_process(post_process.PropertyEquals, 'do_not_retry', True),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'invalid_tests_does_not_prevent_cq_retry',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(
          retry_failed_shards=True,
          swarm_hashes={
              'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
          }),
      # Initial tests & retry shards with patch produce invalid results.
      api.override_step_data(
          'base_unittests (with patch)',
          api.chromium_swarming.canned_summary_output(
              api.test_utils.gtest_results(test_results_json='', retcode=1),
              failure=True)),
      api.override_step_data(
          'base_unittests (retry shards with patch)',
          api.chromium_swarming.canned_summary_output(
              api.test_utils.gtest_results(test_results_json='', retcode=1),
              failure=True)),
      api.post_process(post_process.PropertiesDoNotContain, 'do_not_retry'),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'skip_without_patch_does_not_prevent_cq_retry',
      api.chromium_tests_builder_config.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
          builder_db=ctbc.BuilderDatabase.create({
              'fake-group': {
                  'fake-builder':
                      ctbc.BuilderSpec.create(
                          chromium_config='chromium',
                          gclient_config='chromium',
                      ),
              },
          }),
          try_db=ctbc.TryDatabase.create({
              'fake-try-group': {
                  'fake-try-builder':
                      ctbc.TrySpec.create_for_single_mirror(
                          builder_group='fake-group',
                          buildername='fake-builder',
                          retry_without_patch=False,
                      ),
              },
          }),
      ),
      api.properties(
          retry_failed_shards=True,
          swarm_hashes={
              'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
          }),
      api.override_step_data(
          'base_unittests (with patch)',
          api.chromium_swarming.canned_summary_output(
              api.test_utils.canned_gtest_output(False), failure=True)),
      api.override_step_data(
          'base_unittests (retry shards with patch)',
          api.chromium_swarming.canned_summary_output(
              api.test_utils.canned_gtest_output(False), failure=True)),
      api.post_process(post_process.PropertiesDoNotContain, 'do_not_retry'),
      api.post_process(post_process.DoesNotRun, '.*without patch.*'),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'bot_update_failure_does_not_prevent_cq_retry',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(
          retry_failed_shards=True,
          swarm_hashes={
              'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
          }),
      # Initial tests & retry shards with patch produce invalid results.
      api.override_step_data('bot_update', retcode=1),
      api.post_process(post_process.PropertiesDoNotContain, 'do_not_retry'),
      api.post_process(post_process.DropExpectation),
      api.expect_status('INFRA_FAILURE'),
  )

  # TODO(erikchen): Fix this behavior + test once parallel recipe steps has been
  # implemented.
  # If a test fails in 'with patch', it should be marked as a failing step.
  yield api.test(
      'recipe_step_is_failure_for_failing_test',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(swarm_hashes={
          'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
      }),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests', 'with patch', failures=['Test.One']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests', 'without patch', failures=['Test.One']),
      api.post_process(
          post_process.StepTextContains,
          'base_unittests (test results summary)', [
              'Tests failed with patch, but ignored as they also fail without '
              'patch'
          ]),
      api.post_process(post_process.StepFailure, 'base_unittests (with patch)'),
      api.post_process(post_process.DropExpectation),
  )

  # If a test unexpectedly fails in 'with patch' and then expectedly fails in
  # 'without patch', the build should still fail.
  yield api.test(
      'unexpected_failures_not_forgiven_in_without_patch',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(swarm_hashes={
          'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
      }),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests', 'with patch', failures=['Test.One']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests', 'without patch', expected_failures=['Test.One']),
      api.expect_status('FAILURE'),
      api.post_process(post_process.StepFailure, 'base_unittests (with patch)'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'add_one_test_shard_enabled',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
          experiments=['chromium.add_one_test_shard'],
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(
          shards=20,
          swarm_hashes={
              'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
          }),
      api.post_check(
          api.swarming.check_triggered_request, 'test_pre_run (with patch)' +
          '.[trigger] base_unittests (with patch)', lambda check, req: check(
              req[0].env_vars['GTEST_TOTAL_SHARDS'] == '21')),
      api.post_check(
          api.swarming.check_triggered_request, 'test_pre_run (with patch)' +
          '.[trigger] base_unittests (with patch)',
          lambda check, req: check('experimental_shard_count:21' in req.tags)),
      api.post_check(
          api.swarming.check_triggered_request, 'test_pre_run (with patch)' +
          '.[trigger] base_unittests (with patch)', lambda check, req: check(
              'normally_assigned_shard_count:20' in req.tags)),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'add_one_test_shard_enabled_but_shards_is_1',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
          experiments=['chromium.add_one_test_shard'],
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(
          shards=1,
          swarm_hashes={
              'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
          }),
      api.post_check(
          api.swarming.check_triggered_request, 'test_pre_run (with patch)' +
          '.[trigger] base_unittests (with patch)', lambda check, req: check(
              len(
                  list(
                      filter(
                          lambda s: re.match('experimental_shard_count.*', s),
                          req.tags))) == 0)),
      api.post_check(
          api.swarming.check_triggered_request, 'test_pre_run (with patch)' +
          '.[trigger] base_unittests (with patch)', lambda check, req: check(
              len(
                  list(
                      filter(
                          lambda s: re.match('normally_assigned_shard_count.*',
                                             s), req.tags))) == 0)),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'add_one_test_shard_not_enabled',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(
          shards=20,
          swarm_hashes={
              'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
          }),
      api.post_check(
          api.swarming.check_triggered_request, 'test_pre_run (with patch)' +
          '.[trigger] base_unittests (with patch)', lambda check, req: check(
              req[0].env_vars['GTEST_TOTAL_SHARDS'] == '20')),
      api.post_check(
          api.swarming.check_triggered_request, 'test_pre_run (with patch)' +
          '.[trigger] base_unittests (with patch)', lambda check, req: check(
              len(
                  list(
                      filter(
                          lambda s: re.match('experimental_shard_count.*', s),
                          req.tags))) == 0)),
      api.post_check(
          api.swarming.check_triggered_request, 'test_pre_run (with patch)' +
          '.[trigger] base_unittests (with patch)', lambda check, req: check(
              len(
                  list(
                      filter(
                          lambda s: re.match('normally_assigned_shard_count.*',
                                             s), req.tags))) == 0)),
      api.post_process(post_process.DropExpectation),
  )

  # 'retry without ptach' should dispatch higher priority swarming tasks than
  # 'with patch'.
  yield api.test(
      'retry_swarming_priority',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(swarm_hashes={
          'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
      }),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests', 'with patch', failures=['Test.One']),
      api.post_check(
          api.swarming.check_triggered_request,
          'test_pre_run (with patch).[trigger] base_unittests (with patch)',
          lambda check, req: check(req.priority == 30)),
      api.post_check(
          api.swarming.check_triggered_request,
          'test_pre_run (without patch).[trigger] base_unittests ' +
          '(without patch)', lambda check, req: check(req.priority == 29)),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
  )

  def generate_one_failed_shard_raw():
    shard_zero = api.chromium_swarming.canned_summary_output_raw(
        shard_indices=[0], failure=False)
    shard_one = api.chromium_swarming.canned_summary_output_raw(
        shard_indices=[1], failure=True)
    shards = [shard_zero['shards'][0], shard_one['shards'][0]]
    shards[1]['state'] = 'FAILED'
    return {'shards': shards}

  # If one shard fails or expires, retry shards with patch should retry just
  # that failed/expired shard.
  for failure_type in ['failed', 'expired']:
    test_name = 'retry_shards_with_patch_wait_for_task_' + failure_type

    # This 'with patch' swarming summary contains two shards. First succeeds,
    # second fails.
    swarming_summary = generate_one_failed_shard_raw()
    if failure_type == 'expired':
      swarming_summary['shards'][1]['state'] = 'EXPIRED'
    retry_shards_step_name = (
        'test_pre_run (retry shards with patch).[trigger] base_unittests '
        '(retry shards with patch)')

    # 'retry shards with patch' will only retrigger the second shard. The
    # distinguishing feature is that it has 'custom_task_id' as the task_id.
    retry_trigger_summary = {
        'tasks': [{
            'task_id': 'custom_task_id',
            'request': {
                'name': 'task_name_does_not_matter',
            },
            'task_result': {
                'resultdb_info': {
                    'invocation': 'invocations/custom_task_id',
                }
            },
        },]
    }

    # When collecting the swarming, make sure to update the task_id of shard 1.
    retry_swarming_summary = dict(swarming_summary)
    retry_swarming_summary['shards'][1]['task_id'] = 'custom_task_id'

    base_unittests_retry = 'base_unittests (retry shards with patch)'

    def check_gtest_shard_env(check, req):
      check(req[0].env_vars['GTEST_SHARD_INDEX'] == '1')
      check(req[0].env_vars['GTEST_TOTAL_SHARDS'] == '2')

    # The shard link names contain more than just shard#X since they have timing
    # and state information appended, so look for the prefix
    def does_not_have_shard_0_link(check, steps_dict):
      check(not any(
          l.startswith('shard #0')
          for l in steps_dict[base_unittests_retry].links))

    def has_shard_1_link(check, steps_dict):
      check(
          any(
              l.startswith('shard #1')
              for l in steps_dict[base_unittests_retry].links))

    yield api.test(
        test_name,
        api.platform('linux', 64),
        api.chromium.try_build(
            builder_group='fake-try-group',
            builder='fake-try-builder',
        ),
        ctbc_api.properties(ctbc_api.properties_assembler_for_try_builder()
                            .with_mirrored_builder(
                                builder_group='fake-group',
                                builder='fake-builder',
                            ).assemble()),
        api.properties(
            retry_failed_shards=True,
            shards=2,
            swarm_hashes={
                'base_unittests':
                    'ffffffffffffffffffffffffffffffffffffffff/size',
            }),
        # Override 'with patch' collect step output.
        api.override_step_data(
            'base_unittests (with patch)',
            api.chromium_swarming.summary(
                api.test_utils.canned_gtest_output(passing=False),
                swarming_summary)),

        # Check that we are sending right input to 'retry shards with patch'
        # trigger.
        api.post_process(post_process.LogContains, retry_shards_step_name,
                         'json.output', ['"task_id": "custom_task_id"']),
        api.post_check(api.swarming.check_triggered_request,
                       retry_shards_step_name, check_gtest_shard_env),

        # Override 'retry shards with patch' trigger output.
        api.override_step_data(retry_shards_step_name,
                               api.json.output(retry_trigger_summary)),

        # Override 'retry shards with patch' collect output.
        api.override_step_data(
            'base_unittests (retry shards with patch)',
            api.chromium_swarming.summary(
                api.test_utils.canned_gtest_output(passing=False),
                retry_swarming_summary)),

        # We should not emit a link for shard #0, since it wasn't retried.
        api.post_check(does_not_have_shard_0_link),
        # We should emit a link for shard#1
        api.post_check(has_shard_1_link),
        api.post_process(post_process.DropExpectation),
        api.expect_status('FAILURE'),
    )

  yield api.test(
      'findit_step_layer_flakiness_swarming_custom_dimensions',
      api.platform('win', 64),
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(
          use_custom_dimensions=True,
          retry_failed_shards=True,
          swarm_hashes={
              'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
          }),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests',
          'with patch',
          custom_os='Windows-11-19045',
          failures=['Test.Two']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'findit_step_layer_flakiness_invalid_initial_results',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(
          retry_failed_shards=True,
          swarm_hashes={
              'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
          }),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests', 'with patch', failures=['Test.Two']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests', 'retry shards with patch', failures=['Test.Two']),
      api.chromium_tests.gen_swarming_and_rdb_results('base_unittests',
                                                      'without patch'),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
  )

  # This test tests the scenario when a test failed deterministically with patch
  # (FAILURE, FAILURE), but then passed retry shards with patch in a flaky way
  # (FAILURE, SUCCESS), the test is expected to be labeled as
  # "Step Layer Flakiness".
  yield api.test(
      'findit_step_layer_flakiness_retry_shards_flaky_test',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(
          retry_failed_shards=True,
          swarm_hashes={
              'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
          }),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests', 'with patch', failures=['Test.One']),
      api.post_process(post_process.DropExpectation),
  )


  yield api.test(
      'findit_step_layer_flakiness_retry_shards',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(
          retry_failed_shards=True,
          swarm_hashes={
              'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
          }),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests', 'with patch', failures=['Test.Two']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests', 'retry shards with patch'),
      api.post_process(post_process.DropExpectation),
  )

  # A test that fails in 'with patch', and subsequently succeeds in 'retry with
  # patch' should have a FindIt metadata emission. However, if the test fails
  # with 'NOTRUN', then FindIt wants the test to be ignored.
  for status in ['FAILURE', 'NOTRUN']:
    yield api.test(
        'findit_build_layer_flakiness_' + status,
        api.platform('linux', 64),
        api.chromium.try_build(
            builder_group='fake-try-group',
            builder='fake-try-builder',
        ),
        ctbc_api.properties(ctbc_api.properties_assembler_for_try_builder()
                            .with_mirrored_builder(
                                builder_group='fake-group',
                                builder='fake-builder',
                            ).assemble()),
        api.properties(
            retry_failed_shards=True,
            swarm_hashes={
                'base_unittests':
                    'ffffffffffffffffffffffffffffffffffffffff/size',
            }),
        api.override_step_data(
            'base_unittests (with patch)',
            api.chromium_swarming.canned_summary_output(
                api.json.output({}), failure=True)),
        api.override_step_data(
            'collect tasks (with patch).base_unittests results',
            stdout=api.raw_io.output_text(
                api.test_utils.rdb_results(
                    'base_unittests',
                    failing_tests=['Test.Two'] if status == 'FAILURE' else [],
                    skipped_tests=['Test.Two'] if status == 'NOTRUN' else []))),
        api.override_step_data(
            'base_unittests (retry shards with patch)',
            api.chromium_swarming.canned_summary_output(
                api.json.output({}), failure=True)),
        api.override_step_data(
            'collect tasks (retry shards with patch).base_unittests results',
            stdout=api.raw_io.output_text(
                api.test_utils.rdb_results(
                    'base_unittests',
                    failing_tests=['Test.Two'] if status == 'FAILURE' else [],
                    skipped_tests=['Test.Two'] if status == 'NOTRUN' else []))),
        api.post_process(post_process.DropExpectation),
        api.expect_status('FAILURE'),
    )

  yield api.test(
      'findit_potential_build_layer_flakiness_skip_retry_with_patch',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(swarm_hashes={
          'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
      }),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests', 'with patch', failures=['Test.Two']),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'failure_many_shards',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(
          shards=20,
          swarm_hashes={
              'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
          }),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests', 'with patch', failures=['Test0', 'Test1', 'Test2']),
      api.post_check(
          api.swarming.check_triggered_request, 'test_pre_run (without patch)' +
          '.[trigger] base_unittests (without patch)', lambda check, req: check(
              req[0].env_vars['GTEST_TOTAL_SHARDS'] == '3')),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
  )

  # Any failure in 'retry without patch' should cause the test to be considered
  # flaky on tip of tree, and failures should be ignored.
  yield api.test(
      'retry_without_patch_any_failure',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(swarm_hashes={
          'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
      }),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests', 'with patch', failures=['Test.Two']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests', 'without patch', failures=['Test.Two']),
      api.post_process(post_process.StepTextContains,
                       'base_unittests (test results summary)',
                       ['ignored', 'Test.Two']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'retry_without_patch_with_flaky_failures_and_exit_code',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(swarm_hashes={
          'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
      }),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests', 'with patch', failures=['Test.Two']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests', 'without patch', flaky_failing_tests=['Test.Two']),
      api.post_process(post_process.StepTextContains,
                       'base_unittests (test results summary)',
                       ['ignored', 'Test.Two']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'retry_without_patch_with_all_skips_and_exit_code',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(swarm_hashes={
          'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
      }),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests', 'with patch', failures=['Test.Two']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests', 'without patch', skips=['Test.Two']),
      api.post_process(
          post_process.StepTextContains,
          'base_unittests (test results summary)', [
              'Tests failed with patch, and caused build to fail:<br/>'
              'Test.Two<br/>'
          ]),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'disable_deapply_patch_affected_files',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(
          affected_files=['testing/buildbot/fake-group.json'],
          swarm_hashes={
              'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
          }),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests', 'with patch', failures=['Test.One']),
      api.post_process(post_process.MustRun, 'without patch steps are skipped'),
      api.post_process(
          post_process.SummaryMarkdown,
          '1 Test Suite(s) failed.\n\n**base_unittests** failed because of:'
          '\n\n- Test.One'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'nonzero_exit_code_no_gtest_output',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(
          affected_files=['testing/buildbot/chromium.linux.json'],
          swarm_hashes={
              'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
          }),
      api.override_step_data(
          'base_unittests (with patch)',
          api.chromium_swarming.canned_summary_output(
              api.test_utils.gtest_results(
                  api.json.dumps({'per_iteration_data': []}), retcode=1),
              failure=True)),
      api.post_process(post_process.StepTextEquals, 'base_unittests',
                       'TEST RESULTS WERE INVALID'),
      api.post_process(post_process.SummaryMarkdown,
                       '1 Test Suite(s) failed.\n\n**base_unittests** failed.'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'without_patch_only_retries_relevant_tests',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(
          retry_failed_shards=True,
          swarm_hashes={
              'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
          }),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests', 'with patch', failures=['Test.Two']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests', 'retry shards with patch', failures=['Test.Two']),
      api.post_check(
          api.swarming.check_triggered_request,
          'test_pre_run (without patch).[trigger] base_unittests '
          '(without patch)', lambda check, req: check('--gtest_filter=Test.Two'
                                                      in req[0].command)),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
  )

  # This test tests that only Test.One is determined as unrecoverable failures
  # presented to the users because Test.Two is ignored by
  # "retry shards with patch" and "Test.Three" is ignored by "without patch".
  yield api.test(
      'unrecoverable_failure_results_exclude_ignored_failures',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(
          retry_failed_shards=True,
          swarm_hashes={
              'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
          },
          expected_jsonish_result={
              'status':
                  'FAILURE',
              'summaryMarkdown':
                  ('1 Test Suite(s) failed.\n\n**base_unittests** failed '
                   'because of:\n\n- Test.One'),
          }),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests',
          'with patch',
          failures=['Test.One', 'Test.Two', 'Test.Three']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests',
          'retry shards with patch',
          failures=['Test.One', 'Test.Three']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests', 'without patch', failures=['Test.Three']),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'succeeded_to_exonerate_flaky_failures',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(
          retry_failed_shards=True,
          swarm_hashes={
              'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
          },
          **{
              '$build/test_utils': {
                  'should_exonerate_flaky_failures': True,
              },
          }),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests', 'with patch', failures=['Test.Two']),
      api.luci_analysis.query_failure_rate_results([
          api.luci_analysis.generate_analysis(
              test_id='ninja://base_unittests/Test.Two',
              expected_count=0,
              unexpected_count=10),
      ]),
      api.post_process(post_process.MustRun, 'base_unittests (with patch)'),
      api.post_process(post_process.DoesNotRun,
                       'base_unittests (retry shards with patch)'),
      api.post_process(post_process.DoesNotRun,
                       'base_unittests (without patch)'),
      api.post_process(
          post_process.StepTextContains,
          'base_unittests (test results summary)', [
              'Tests failed with patch, but ignored as they are known to be '
              'flaky (see Test Results Tab for more info):<br/>Test.Two<br/>'
          ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'failed_to_exonerate_flaky_failures',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(
          retry_failed_shards=True,
          swarm_hashes={
              'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
          },
          **{
              '$build/test_utils': {
                  'should_exonerate_flaky_failures': True,
              },
          }),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests', 'with patch', failures=['Test.Two']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests', 'retry shards with patch', failures=['Test.Two']),
      api.post_process(post_process.MustRun, 'base_unittests (with patch)'),
      api.post_process(post_process.MustRun,
                       'base_unittests (retry shards with patch)'),
      api.post_process(post_process.MustRun, 'base_unittests (without patch)'),
      api.post_process(
          post_process.StepTextContains,
          'base_unittests (test results summary)', [
              'Tests failed with patch, and caused build to fail:<br/>'
              'Test.Two<br/>'
          ]),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
  )

  # This test tests the scenario that if a known flaky failure fails again while
  # retrying, it doesn't fail a test suite as long as there are no other
  # non-flaky failures. For example: t1 and t2 failed "with patch", and t2 is
  # known to be flaky, while retrying, t1 succeeds but t2 fails again, and the
  # build is expected to be succeed without running "without patch" steps.
  yield api.test(
      'known_flaky_failure_failed_again_while_retrying',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(
          retry_failed_shards=True,
          swarm_hashes={
              'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
          },
          **{
              '$build/test_utils': {
                  'should_exonerate_flaky_failures': True,
              },
          }),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests', 'with patch', failures=['Test.One', 'Test.Two']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests', 'retry shards with patch', failures=['Test.Two']),
      api.luci_analysis.query_failure_rate_results([
          api.luci_analysis.generate_analysis(
              test_id='ninja://base_unittests/Test.One',
              expected_count=10,
              unexpected_count=0),
          api.luci_analysis.generate_analysis(
              test_id='ninja://base_unittests/Test.Two',
              expected_count=10,
              unexpected_count=0,
              flaky_verdict_counts=[5, 20]),
      ]),
      api.post_process(post_process.MustRun, 'base_unittests (with patch)'),
      api.post_process(post_process.MustRun,
                       'base_unittests (retry shards with patch)'),
      api.post_process(post_process.DoesNotRun,
                       'base_unittests (without patch)'),
      api.post_process(
          post_process.StepTextContains,
          'base_unittests (test results summary)', [
              'Tests failed with patch, but ignored as they are known to be '
              'flaky (see Test Results Tab for more info):<br/>Test.Two<br/>'
          ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'known_flaky_failure_does_not_run_again',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(
          retry_failed_shards=True,
          swarm_hashes={
              'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
          },
          **{
              '$build/test_utils': {
                  'should_exonerate_flaky_failures': True,
              },
          }),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests', 'with patch', failures=['Test.One', 'Test.Two']),
      api.luci_analysis.query_failure_rate_results([
          api.luci_analysis.generate_analysis(
              test_id='ninja://base_unittests/Test.One',
              expected_count=0,
              unexpected_count=10,
          ),
          api.luci_analysis.generate_analysis(
              test_id='ninja://base_unittests/Test.Two',
              expected_count=0,
              unexpected_count=10,
          ),
      ]),
      api.post_process(post_process.MustRun, 'base_unittests (with patch)'),
      api.post_process(post_process.DoesNotRun,
                       'base_unittests (retry shards with patch)'),
      api.post_process(post_process.DoesNotRun,
                       'base_unittests (without patch)'),
      api.post_process(post_process.DropExpectation),
  )

  # This test tests the scenario that a known flaky failure shouldn't be retried
  # "without patch". For example: t1, t2, t3 failed "with patch", and t2 is
  # known to be flaky and t3 is known to be deterministically failing.
  # While retrying, t1, t2, t3 fails again, and only t1 is
  # expected to be retried during "without patch".
  yield api.test(
      'without_patch_only_retries_non_flaky_and_not_trunk_failing_failures',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(
          retry_failed_shards=True,
          swarm_hashes={
              'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
          },
          **{
              '$build/test_utils': {
                  'should_exonerate_flaky_failures': True,
              },
          }),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests',
          'with patch',
          failures=['Test.One', 'Test.Two', 'Test.Three']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests',
          'retry shards with patch',
          failures=['Test.One', 'Test.Two', 'Test.Three']),
      api.luci_analysis.query_failure_rate_results([
          api.luci_analysis.generate_analysis(
              test_id='ninja://base_unittests/Test.One',
              expected_count=10,
              unexpected_count=0),
          api.luci_analysis.generate_analysis(
              test_id='ninja://base_unittests/Test.Two',
              expected_count=10,
              unexpected_count=0,
              flaky_verdict_counts=[5, 20]),
          api.luci_analysis.generate_analysis(
              test_id='ninja://base_unittests/Test.Three',
              expected_count=10,
              unexpected_count=20),
      ]),
      api.post_process(post_process.MustRun, 'base_unittests (with patch)'),
      api.post_process(post_process.MustRun,
                       'base_unittests (retry shards with patch)'),
      api.post_process(post_process.MustRun, 'base_unittests (without patch)'),
      api.post_check(
          api.swarming.check_triggered_request,
          'test_pre_run (without patch).[trigger] base_unittests '
          '(without patch)', lambda check, req: check('--gtest_filter=Test.One'
                                                      in req[0].command)),
      api.post_process(
          post_process.StepCommandDoesNotContain,
          'test_pre_run (without patch).[trigger] base_unittests '
          '(without patch)',
          'Test.Two',
      ),
      api.post_process(
          post_process.StepCommandDoesNotContain,
          'test_pre_run (without patch).[trigger] base_unittests '
          '(without patch)',
          'Test.Three',
      ),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
  )

  # This test tests the scenrio when there are multiple test suites with
  # failures and that after the "without patch" steps, there are two different
  # kinds test suites need to summarize their results:
  # 1. Those ran "without patch" steps because there are non-forgivable failures
  #    after "with patch" steps.
  # 2. Those didn't run "without patch" steps because their failures are known
  #    flaky tests and are forgiven.
  # The test results of these two kinds should both be summarized correctly.
  yield api.test(
      'summarize_both_retried_and_not_retried_test_suites',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(
          retry_failed_shards=True,
          additional_gtest_targets=['component_unittests', 'url_unittests'],
          swarm_hashes={
              'base_unittests':
                  'ffffffffffffffffffffffffffffffffffffffff/size',
              'component_unittests':
                  'cccccccccccccccccccccccccccccccccccccccc/size',
              'url_unittests':
                  'dddddddddddddddddddddddddddddddddddddddd/size',
          },
          **{
              '$build/test_utils': {
                  'should_exonerate_flaky_failures': True,
              },
          }),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests', 'with patch', failures=['BaseTest.One']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'url_unittests', 'with patch', failures=['UrlTest.One']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'url_unittests', 'retry shards with patch', failures=['UrlTest.One']),
      api.luci_analysis.query_failure_rate_results([
          api.luci_analysis.generate_analysis(
              test_id='ninja://base_unittests/BaseTest.One',
              expected_count=10,
              unexpected_count=0,
              flaky_verdict_counts=[5, 25]),
      ]),
      api.post_process(
          post_process.StepTextContains,
          'base_unittests (test results summary)', [
              'Tests failed with patch, but ignored as they are known to be '
              'flaky (see Test Results Tab for more info):'
              '<br/>BaseTest.One<br/>'
          ]),
      api.post_process(post_process.StepTextContains,
                       'url_unittests (test results summary)', [
                           'Tests failed with patch, and caused build to fail:'
                           '<br/>UrlTest.One<br/>'
                       ]),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
  )
