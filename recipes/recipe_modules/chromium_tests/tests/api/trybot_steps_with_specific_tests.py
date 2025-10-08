# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

# TODO(gbeaty) This file doesn't make sense, there's no clear
# distinction between the test cases of this file and trybot_steps.py.
# It would make sense to merge them into a single file or into separate
# files with a more cohesive groupings of test cases.

from __future__ import annotations

import re

from recipe_engine import post_process

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

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


def RunSteps(api):
  builder_id, builder_config = (
      api.chromium_tests_builder_config.lookup_builder())
  return api.chromium_tests.trybot_steps(builder_id, builder_config)


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
          ctbc_api.properties_assembler_for_try_builder(
              retry_failed_shards=False).with_mirrored_builder(
                  builder_group='fake-group',
                  builder='fake-builder',
              ).assemble()),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'base_unittests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                          },
                      },
                  }],
              },
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
      'compile_failure',
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
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'base_unittests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                          },
                      },
                  }],
              },
          }),
      api.step_data('compile (with patch)', retcode=1),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'without_patch_compile_failure',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder(
              retry_failed_shards=False).with_mirrored_builder(
                  builder_group='fake-group',
                  builder='fake-builder',
              ).assemble()),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'base_unittests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                          },
                      },
                  }],
              },
          }),
      api.step_data('compile (without patch)', retcode=1),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests', 'with patch', failures=['Test.One']),
      api.expect_status('FAILURE'),
      api.post_check(post_process.MustRun, 'clobber'),
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
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'base_unittests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                          },
                      },
                  }],
              },
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
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'base_unittests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                          },
                      },
                  }],
              },
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
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'base_unittests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                          },
                      },
                  }],
              },
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
          ctbc_api.properties_assembler_for_try_builder(
              retry_failed_shards=False).with_mirrored_builder(
                  builder_group='fake-group',
                  builder='fake-builder',
              ).assemble()),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'base_unittests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                          },
                      },
                  }],
              },
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
          ctbc_api.properties_assembler_for_try_builder(
              retry_failed_shards=False).with_mirrored_builder(
                  builder_group='fake-group',
                  builder='fake-builder',
              ).assemble()),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'base_unittests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                          },
                      },
                  }],
              },
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
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'base_unittests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                          },
                          'shards': 20,
                      },
                  }],
              },
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
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'base_unittests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                          },
                      },
                  }],
              },
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
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'base_unittests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                          },
                          'shards': 20,
                      },
                  }],
              },
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
          ctbc_api.properties_assembler_for_try_builder(
              retry_failed_shards=False).with_mirrored_builder(
                  builder_group='fake-group',
                  builder='fake-builder',
              ).assemble()),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'base_unittests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                          },
                      },
                  }],
              },
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

  yield api.test(
      'retry_only_failed_tests',
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
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'base_unittests',
                      'retry_only_failed_tests': True,
                      'swarming': {
                          'shards': 2,
                          'dimensions': {
                              'os': 'Linux',
                          },
                      },
                  }],
              },
          }),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests',
          'with patch',
          failures=['Test.One'],
          successes=['Test.Two'],
      ),
      api.post_check(
          api.swarming.check_triggered_request,
          'test_pre_run (retry shards with patch)' +
          '.[trigger] base_unittests (retry shards with patch)', lambda check,
          req: check('GTEST_TOTAL_SHARDS' not in req[0].env_vars)),
      api.post_process(
          post_process.LogContains,
          'test_pre_run (retry shards with patch).[trigger] base_unittests '
          '(retry shards with patch)',
          'json.input',
          ['--gtest_filter', 'Test.One'],
      ),
      api.post_process(
          post_process.LogDoesNotContain,
          'test_pre_run (retry shards with patch).[trigger] base_unittests '
          '(retry shards with patch)',
          'json.input',
          ['Test.Two'],
      ),
      api.post_process(post_process.DropExpectation),
  )

  VeryLongTestName = 'VeryLo' + 'o' * 90 + 'ngTestName.'
  yield api.test(
      'retry_only_failed_tests_with_long_filter_list',
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
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'base_unittests',
                      'retry_only_failed_tests': True,
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                          },
                      },
                  }],
              },
          }),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests',
          'with patch',
          failures=[VeryLongTestName + str(i) for i in range(1000)],
          successes=['Test.Two'],
      ),
      api.post_process(
          post_process.LogDoesNotContain,
          'test_pre_run (retry shards with patch).[trigger] base_unittests '
          '(retry shards with patch)',
          'json.input',
          ['--gtest_filter'],
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'retry_only_failed_tests_unless_invalid_failures',
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
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'base_unittests',
                      'retry_only_failed_tests': True,
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                          },
                          'shards': 10,
                      },
                  }],
              },
          }),
      # Initial tests & retry shards with patch produce invalid results.
      api.override_step_data(
          'base_unittests (with patch)',
          api.chromium_swarming.canned_summary_output(
              api.test_utils.gtest_results(test_results_json='', retcode=1),
              failure=True)),
      api.post_process(
          post_process.LogDoesNotContain,
          'test_pre_run (retry shards with patch).[trigger] base_unittests '
          '(retry shards with patch)',
          'json.input',
          ['--gtest_filter'],
      ),
      api.post_check(
          api.swarming.check_triggered_request,
          'test_pre_run (retry shards with patch).[trigger] base_unittests '
          '(retry shards with patch)', lambda check, req: check(req[0].env_vars[
              'GTEST_TOTAL_SHARDS'] == '10')),
      api.post_process(post_process.DropExpectation),
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
  for failure_type, build_status in (
      ('failed', 'FAILURE'),
      ('expired', 'INFRA_FAILURE'),
  ):
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
        api.chromium_tests.read_targets_spec(
            'fake-group', {
                'fake-builder': {
                    'gtest_tests': [{
                        'test': 'base_unittests',
                        'swarming': {
                            'dimensions': {
                                'os': 'Linux',
                            },
                            'shards': 2,
                        },
                    }],
                },
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
        api.expect_status(build_status),
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
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'base_unittests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Windows-11-19045',
                          },
                      },
                  }],
              },
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
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'base_unittests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                          },
                      },
                  }],
              },
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
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'base_unittests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                          },
                      },
                  }],
              },
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
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'base_unittests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                          },
                      },
                  }],
              },
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
        api.chromium_tests.read_targets_spec(
            'fake-group', {
                'fake-builder': {
                    'gtest_tests': [{
                        'test': 'base_unittests',
                        'swarming': {
                            'dimensions': {
                                'os': 'Linux',
                            },
                        },
                    }],
                },
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
          ctbc_api.properties_assembler_for_try_builder(
              retry_failed_shards=False).with_mirrored_builder(
                  builder_group='fake-group',
                  builder='fake-builder',
              ).assemble()),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'base_unittests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                          },
                      },
                  }],
              },
          }),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests', 'with patch', failures=['Test.Two']),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'failure_many_shards',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder(
              retry_failed_shards=False).with_mirrored_builder(
                  builder_group='fake-group',
                  builder='fake-builder',
              ).assemble()),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'base_unittests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                          },
                          'shards': 20,
                      },
                  }],
              },
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
          ctbc_api.properties_assembler_for_try_builder(
              retry_failed_shards=False).with_mirrored_builder(
                  builder_group='fake-group',
                  builder='fake-builder',
              ).assemble()),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'base_unittests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                          },
                      },
                  }],
              },
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
          ctbc_api.properties_assembler_for_try_builder(
              retry_failed_shards=False).with_mirrored_builder(
                  builder_group='fake-group',
                  builder='fake-builder',
              ).assemble()),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'base_unittests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                          },
                      },
                  }],
              },
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

  def StepTextDoesNotContain(check, step_odict, step, unexpected_substrs):
    for unexpected in unexpected_substrs:
      check(unexpected not in step_odict[step].step_text)

  yield api.test(
      'retry_without_patch_with_all_skips_and_exit_code',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder(
              retry_failed_shards=False).with_mirrored_builder(
                  builder_group='fake-group',
                  builder='fake-builder',
              ).assemble()),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'base_unittests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                          },
                      },
                  }],
              },
          }),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests',
          'with patch',
          failures=['Test.Two'],
          successes=['Test.One']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests', 'without patch', skips=['Test.Two']),
      api.post_process(
          post_process.StepTextContains,
          'base_unittests (test results summary)',
          ['Test.Two'],
      ),
      api.post_process(
          StepTextDoesNotContain,
          'base_unittests (test results summary)',
          ['Test.One'],
      ),
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
          ctbc_api.properties_assembler_for_try_builder(
              retry_failed_shards=False).with_mirrored_builder(
                  builder_group='fake-group',
                  builder='fake-builder',
              ).assemble()),
      api.tryserver.get_files_affected_by_patch(
          ['testing/buildbot/fake-group.json']),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'base_unittests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                          },
                      },
                  }],
              },
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
          ctbc_api.properties_assembler_for_try_builder(
              retry_failed_shards=False).with_mirrored_builder(
                  builder_group='fake-group',
                  builder='fake-builder',
              ).assemble()),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'base_unittests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                          },
                      },
                  }],
              },
          }),
      api.override_step_data(
          'base_unittests (with patch)',
          api.chromium_swarming.canned_summary_output(
              api.test_utils.gtest_results(
                  api.json.dumps({'per_iteration_data': []}), retcode=1),
              failure=True)),
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
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'base_unittests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                          },
                      },
                  }],
              },
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
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'base_unittests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                          },
                      },
                  }],
              },
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
      api.expect_status('FAILURE'),
      api.post_check(
          post_process.SummaryMarkdown,
          ('1 Test Suite(s) failed.\n\n**base_unittests** failed because of:'
           '\n\n- Test.One')),
      api.post_process(post_process.DropExpectation),
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
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'base_unittests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                          },
                      },
                  }],
              },
          }),
      api.properties(**{
          '$build/test_utils': {
              'should_exonerate_flaky_failures': True,
          },
      }),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests',
          'with patch',
          failures=['Test.Two'],
          successes=['Test.One']),
      api.override_step_data(
          'query LUCI Analysis for stability.rpc call',
          stdout=api.json.output(
              api.luci_analysis.generate_stability_response([
                  api.luci_analysis.generate_stability_analysis(
                      test_id='ninja://base_unittests/Test.Two',
                      failure_rate_is_met=True,
                      flake_rate_is_met=True,
                  )
              ])),
      ),
      api.post_process(post_process.MustRun, 'base_unittests (with patch)'),
      api.post_process(post_process.DoesNotRun,
                       'base_unittests (retry shards with patch)'),
      api.post_process(post_process.DoesNotRun,
                       'base_unittests (without patch)'),
      api.post_process(
          post_process.StepTextContains,
          'base_unittests (test results summary)',
          ['Test.Two'],
      ),
      api.post_process(
          StepTextDoesNotContain,
          'base_unittests (test results summary)',
          ['Test.One'],
      ),
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
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'base_unittests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                          },
                      },
                  }],
              },
          }),
      api.properties(**{
          '$build/test_utils': {
              'should_exonerate_flaky_failures': True,
          },
      }),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests',
          'with patch',
          failures=['Test.Two'],
          successes=['Test.One']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests', 'retry shards with patch', failures=['Test.Two']),
      api.post_process(post_process.MustRun, 'base_unittests (with patch)'),
      api.post_process(post_process.MustRun,
                       'base_unittests (retry shards with patch)'),
      api.post_process(post_process.MustRun, 'base_unittests (without patch)'),
      api.post_process(
          post_process.StepTextContains,
          'base_unittests (test results summary)',
          ['Test.Two'],
      ),
      api.post_process(
          StepTextDoesNotContain,
          'base_unittests (test results summary)',
          ['Test.One'],
      ),
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
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'base_unittests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                          },
                      },
                  }],
              },
          }),
      api.properties(**{
          '$build/test_utils': {
              'should_exonerate_flaky_failures': True,
          },
      }),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests', 'with patch', failures=['Test.One', 'Test.Two']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests', 'retry shards with patch', failures=['Test.Two']),
      api.override_step_data(
          'query LUCI Analysis for stability.rpc call',
          stdout=api.json.output(
              api.luci_analysis.generate_stability_response([
                  api.luci_analysis.generate_stability_analysis(
                      test_id='ninja://base_unittests/Test.One'),
                  api.luci_analysis.generate_stability_analysis(
                      test_id='ninja://base_unittests/Test.Two',
                      flake_rate_is_met=True,
                  ),
              ])),
      ),
      api.post_process(post_process.MustRun, 'base_unittests (with patch)'),
      api.post_process(post_process.MustRun,
                       'base_unittests (retry shards with patch)'),
      api.post_process(post_process.DoesNotRun,
                       'base_unittests (without patch)'),
      api.post_process(
          post_process.StepTextContains,
          'base_unittests (test results summary)',
          ['Test.Two'],
      ),
      api.post_process(
          StepTextDoesNotContain,
          'base_unittests (test results summary)',
          ['Test.One'],
      ),
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
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'base_unittests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                          },
                      },
                  }],
              },
          }),
      api.properties(**{
          '$build/test_utils': {
              'should_exonerate_flaky_failures': True,
          },
      }),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests', 'with patch', failures=['Test.One', 'Test.Two']),
      api.override_step_data(
          'query LUCI Analysis for stability.rpc call',
          stdout=api.json.output(
              api.luci_analysis.generate_stability_response([
                  api.luci_analysis.generate_stability_analysis(
                      test_id='ninja://base_unittests/Test.One',
                      failure_rate_is_met=True,
                      flake_rate_is_met=True,
                  ),
                  api.luci_analysis.generate_stability_analysis(
                      test_id='ninja://base_unittests/Test.Two',
                      failure_rate_is_met=True,
                      flake_rate_is_met=True,
                  ),
              ])),
      ),
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
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'base_unittests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                          },
                      },
                  }],
              },
          }),
      api.properties(**{
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
      api.override_step_data(
          'query LUCI Analysis for stability.rpc call',
          stdout=api.json.output(
              api.luci_analysis.generate_stability_response([
                  api.luci_analysis.generate_stability_analysis(
                      test_id='ninja://base_unittests/Test.One'),
                  api.luci_analysis.generate_stability_analysis(
                      test_id='ninja://base_unittests/Test.Two',
                      failure_rate_is_met=True,
                      flake_rate_is_met=True,
                  ),
                  api.luci_analysis.generate_stability_analysis(
                      test_id='ninja://base_unittests/Test.Three',
                      failure_rate_is_met=True,
                      flake_rate_is_met=True,
                  ),
              ])),
      ),
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
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'base_unittests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                          },
                      },
                  }, {
                      'test': 'component_unittests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                          },
                      },
                  }, {
                      'test': 'url_unittests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                          },
                      },
                  }],
              },
          }),
      api.properties(**{
          '$build/test_utils': {
              'should_exonerate_flaky_failures': True,
          },
      }),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests',
          'with patch',
          failures=['BaseTest.One'],
          successes=['BaseTest.Two']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'url_unittests',
          'with patch',
          failures=['UrlTest.One'],
          successes=['UrlTest.Two']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'url_unittests', 'retry shards with patch', failures=['UrlTest.One']),
      api.override_step_data(
          'query LUCI Analysis for stability.rpc call',
          stdout=api.json.output(
              api.luci_analysis.generate_stability_response([
                  api.luci_analysis.generate_stability_analysis(
                      test_id='ninja://base_unittests/BaseTest.One',
                      failure_rate_is_met=True,
                      flake_rate_is_met=True,
                  ),
                  api.luci_analysis.generate_stability_analysis(
                      test_id='ninja://base_unittests/UrlTest.One'),
              ])),
      ),
      api.post_process(
          post_process.StepTextContains,
          'base_unittests (test results summary)',
          ['BaseTest.One'],
      ),
      api.post_process(
          StepTextDoesNotContain,
          'base_unittests (test results summary)',
          ['BaseTest.Two'],
      ),
      api.post_process(
          post_process.StepTextContains,
          'url_unittests (test results summary)',
          ['UrlTest.One'],
      ),
      api.post_process(
          StepTextDoesNotContain,
          'url_unittests (test results summary)',
          ['UrlTest.Two'],
      ),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
  )
