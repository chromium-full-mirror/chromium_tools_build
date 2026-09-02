# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process
from PB.recipe_modules.build.chromium_orchestrator.properties import (
    InputProperties)

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
    chromium,
    chromium_orchestrator,
    chromium_swarming,
    chromium_tests,
    chromium_tests_builder_config,
    code_coverage,
    filter as filter_module,
    profiles,
    test_utils,
)
from RECIPE_MODULES.depot_tools import gclient, gitiles, tryserver
from RECIPE_MODULES.recipe_engine import (
    buildbucket,
    cv,
    json,
    luci_analysis,
    path,
    properties,
    raw_io,
    step,
    swarming,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  chromium: chromium.API
  chromium_orchestrator: chromium_orchestrator.API
  chromium_swarming: chromium_swarming.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  code_coverage: code_coverage.API
  cv: cv.API
  filter: filter_module.API
  gclient: gclient.API
  gitiles: gitiles.API
  json: json.API
  luci_analysis: luci_analysis.API
  path: path.API
  profiles: profiles.API
  properties: properties.API
  raw_io: raw_io.API
  step: step.API
  swarming: swarming.API
  test_utils: test_utils.API
  tryserver: tryserver.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  chromium: chromium.TEST_API
  chromium_orchestrator: chromium_orchestrator.TEST_API
  chromium_swarming: chromium_swarming.TEST_API
  chromium_tests: chromium_tests.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  json: json.TEST_API
  luci_analysis: luci_analysis.TEST_API
  properties: properties.TEST_API
  raw_io: raw_io.TEST_API
  swarming: swarming.TEST_API
  test_utils: test_utils.TEST_API


def RunSteps(api: DEPS):
  assert api.tryserver.is_tryserver
  api.chromium_swarming.set_default_dimension('os', 'Linux')
  return api.chromium_orchestrator.trybot_steps()


def GenTests(api: TEST_DEPS):
  ctbc_api = api.chromium_tests_builder_config

  def ctbc_properties(**kwargs):
    return ctbc_api.properties(
        ctbc_api.properties_assembler_for_try_builder(
            **kwargs).with_mirrored_builder(
                builder_group='fake-group',
                builder='fake-builder',
            ).with_mirrored_tester(
                builder_group='fake-group',
                builder='fake-tester',
            ).assemble())

  yield api.test(
      'test_failures_prevent_cq_retry',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester'),
      api.chromium_orchestrator.override_compilator_steps(),
      api.chromium_orchestrator.override_compilator_steps(
          with_patch=True, is_compile_phase=False),
      api.chromium_orchestrator.override_compilator_steps(with_patch=False),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', 'with patch', failures=['test_case1']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', 'retry shards with patch', failures=['test_case1']),
      api.post_process(post_process.PropertyEquals, 'do_not_retry', True),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'invalid_tests_do_not_prevent_cq_retry',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester'),
      api.chromium_orchestrator.override_compilator_steps(),
      api.chromium_orchestrator.override_compilator_steps(
          with_patch=True, is_compile_phase=False),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', 'with patch', failures=['test_case1']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', 'retry shards with patch', failures=['test_case1']),
      api.post_process(post_process.PropertiesDoNotContain, 'do_not_retry'),
      api.post_process(post_process.DropExpectation),
      api.expect_status('INFRA_FAILURE'),
  )

  yield api.test(
      'expired_with_patch_and_valid_failures',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester'),
      api.chromium_orchestrator.override_compilator_steps(),
      api.chromium_orchestrator.override_compilator_steps(
          with_patch=True, is_compile_phase=False),
      api.chromium_orchestrator.override_compilator_steps(with_patch=False),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests',
          'with patch',
          failures=['test_case1'],
          internal_failure=True),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', 'retry shards with patch', failures=['test_case1']),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'expired_both_suffixes_and_valid_failures',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester'),
      api.chromium_orchestrator.override_compilator_steps(),
      api.chromium_orchestrator.override_compilator_steps(
          with_patch=True, is_compile_phase=False),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests',
          'with patch',
          failures=['test_case1'],
          internal_failure=True),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests',
          'retry shards with patch',
          failures=['test_case1'],
          internal_failure=True),
      api.post_process(post_process.DropExpectation),
      api.expect_status('INFRA_FAILURE'),
  )

  yield api.test(
      'expired_with_patch_and_valid_failures_exonerated',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester'),
      api.chromium_orchestrator.override_compilator_steps(),
      api.chromium_orchestrator.override_compilator_steps(
          with_patch=True, is_compile_phase=False),
      api.chromium_orchestrator.override_compilator_steps(with_patch=False),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests',
          'with patch',
          failures=['test_case1'],
          internal_failure=True),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', 'retry shards with patch', failures=['test_case2']),
      api.luci_analysis.query_failure_rate_results([
          api.luci_analysis.generate_analysis(
              test_id='ninja://browser_tests/test_case1',
              expected_count=0,
              unexpected_count=10),
      ]),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'expired_both_suffixes_and_valid_failures_exonerated',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester'),
      api.chromium_orchestrator.override_compilator_steps(),
      api.chromium_orchestrator.override_compilator_steps(
          with_patch=True, is_compile_phase=False),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests',
          'with patch',
          failures=['test_case1'],
          internal_failure=True),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', 'retry shards with patch', internal_failure=True),
      api.luci_analysis.query_failure_rate_results([
          api.luci_analysis.generate_analysis(
              test_id='ninja://browser_tests/test_case1',
              expected_count=0,
              unexpected_count=10),
      ]),
      api.post_process(post_process.DropExpectation),
      api.expect_status('INFRA_FAILURE'),
  )

  yield api.test(
      'skip_without_patch_does_not_prevent_cq_retry',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      ctbc_properties(retry_without_patch=False),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester'),
      api.chromium_orchestrator.override_compilator_steps(),
      api.chromium_orchestrator.override_compilator_steps(
          is_compile_phase=False),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', 'with patch', failures=['test_case1']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', 'retry shards with patch', failures=['test_case1']),
      api.post_process(post_process.DoesNotRun,
                       'trigger compilator (without patch)'),
      api.post_process(post_process.PropertiesDoNotContain, 'do_not_retry'),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
  )

  # TODO(erikchen): Fix this behavior + test once parallel recipe steps has been
  # implemented.
  # If a test fails in 'with patch', it should be marked as a failing step.
  yield api.test(
      'recipe_step_is_failure_for_failing_test',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester'),
      api.chromium_orchestrator.override_compilator_steps(),
      api.chromium_orchestrator.override_compilator_steps(
          with_patch=True, is_compile_phase=False),
      api.chromium_orchestrator.override_compilator_steps(with_patch=False),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', 'with patch', failures=['test_case1']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', 'retry shards with patch', failures=['test_case1']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', 'without patch', failures=['test_case1']),
      api.post_process(post_process.StepFailure, 'browser_tests (with patch)'),
      api.post_process(post_process.DropExpectation),
  )

  # 'retry without patch' should dispatch higher priority swarming tasks than
  # 'with patch'.
  yield api.test(
      'retry_swarming_priority',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester'),
      api.chromium_orchestrator.override_compilator_steps(),
      api.chromium_orchestrator.override_compilator_steps(
          with_patch=True, is_compile_phase=False),
      api.chromium_orchestrator.override_compilator_steps(with_patch=False),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', 'with patch', failures=['test_case1']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', 'retry shards with patch', failures=['test_case1']),
      api.post_check(
          api.swarming.check_triggered_request,
          'test_pre_run (with patch).[trigger] browser_tests (with patch)',
          lambda check, req: check(req.priority == 30)),
      api.post_check(
          api.swarming.check_triggered_request,
          'test_pre_run (without patch).[trigger] browser_tests ' +
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

  def maybe_override_without_patch_compilator(failure_type):
    if failure_type == 'failed':
      return api.chromium_orchestrator.override_compilator_steps(
          with_patch=False)
    return api.empty_test_data()

  # If one shard fails or expires, retry shards with patch should retry just
  # that failed/expired shard.
  for failure_type, expected_status in [
      ('failed', 'FAILURE'),
      ('expired', 'INFRA_FAILURE'),
  ]:
    test_name = 'retry_shards_with_patch_wait_for_task_' + failure_type

    # This 'with patch' swarming summary contains two shards. First succeeds,
    # second fails.
    swarming_summary = generate_one_failed_shard_raw()
    if failure_type == 'expired':
      swarming_summary['shards'][1]['state'] = 'EXPIRED'
    retry_shards_step_name = (
        'test_pre_run (retry shards with patch).[trigger] browser_tests '
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
    retry_swarming_summary = {
        'shards': [
            api.chromium_swarming.canned_summary_output_raw(
                shard_indices=[0], failure=True)['shards'][0]
        ]
    }
    retry_swarming_summary['shards'][0]['task_id'] = 'custom_task_id'

    browser_tests_retry = 'browser_tests (retry shards with patch)'

    # The shard link names contain more than just shard#X since they have timing
    # and state information appended, so look for the prefix
    def has_shard_0_link(check, steps_dict):
      check(
          any(
              l.startswith('shard #0')
              for l in steps_dict[browser_tests_retry].links))

    def does_not_have_shard_1_link(check, steps_dict):
      check(not any(
          l.startswith('shard #1')
          for l in steps_dict[browser_tests_retry].links))

    yield api.test(
        test_name,
        api.chromium.try_build(
            builder_group='fake-try-group',
            builder='fake-orchestrator',
            tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
        ),
        ctbc_properties(),
        api.properties(
            **{
                '$build/chromium_orchestrator':
                    InputProperties(
                        compilator='fake-compilator',
                        compilator_watcher_git_revision='e841fc',
                    ),
            }),
        api.chromium_orchestrator.override_test_spec(
            builder_group='fake-group',
            builder='fake-builder',
            tester='fake-tester',
            shards=2),
        api.chromium_orchestrator.override_compilator_steps(),
        api.chromium_orchestrator.override_compilator_steps(
            with_patch=True, is_compile_phase=False),
        maybe_override_without_patch_compilator(failure_type),
        # Override 'with patch' collect step output. We override it manually
        # here rather than using gen_swarming_and_rdb_results() since we need
        # to tweak the amount of shards used.
        api.override_step_data(
            'browser_tests (with patch)',
            api.chromium_swarming.summary(
                api.json.output({}), swarming_summary)),
        api.override_step_data(
            'collect tasks (with patch).browser_tests results',
            stdout=api.raw_io.output_text(
                api.test_utils.rdb_results(
                    'browser_tests', failing_tests=['Test.One']))),

        # Check that we are sending right input to 'retry shards with patch'
        # trigger.
        api.post_process(post_process.LogContains, retry_shards_step_name,
                         'json.output', ['"task_id": "custom_task_id"']),

        # Override 'retry shards with patch' trigger output.
        api.override_step_data(retry_shards_step_name,
                               api.json.output(retry_trigger_summary)),

        # Override 'retry shards with patch' collect output.
        api.override_step_data(
            'browser_tests (retry shards with patch)',
            api.chromium_swarming.summary(
                api.json.output({}), retry_swarming_summary)),
        api.override_step_data(
            'collect tasks (retry shards with patch).browser_tests results',
            stdout=api.raw_io.output_text(
                api.test_utils.rdb_results(
                    'browser_tests', failing_tests=['Test.One']))),

        # We should emit a link for shard #0 but not for shard #1, since all
        # failed tests get grouped into one shard during retry.
        api.post_check(has_shard_0_link),
        api.post_check(does_not_have_shard_1_link),
        api.post_process(post_process.DropExpectation),
        api.expect_status(expected_status),
    )

  def StepTextDoesNotContain(check, step_odict, step, unexpected_substrs):
    for unexpected in unexpected_substrs:
      check(unexpected not in step_odict[step].step_text)

  yield api.test(
      'succeeded_to_exonerate_flaky_failures',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
          tests=['base_unittests']),
      api.properties(**{
          '$build/test_utils': {
              'should_exonerate_flaky_failures': True,
          },
      }),
      api.chromium_orchestrator.override_compilator_steps(
          tests=['base_unittests']),
      api.chromium_orchestrator.override_compilator_steps(
          with_patch=True, is_compile_phase=False),
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
                  ),
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
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
          tests=['base_unittests']),
      api.chromium_orchestrator.override_compilator_steps(
          tests=['base_unittests']),
      api.chromium_orchestrator.override_compilator_steps(
          with_patch=True, is_compile_phase=False),
      api.chromium_orchestrator.override_compilator_steps(
          with_patch=False, tests=['base_unittests']),
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
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  # This test tests the scenario that if a known flaky failure fails again while
  # retrying, it doesn't fail a test suite as long as there are no other
  # non-flaky failures. For example: t1 and t2 failed "with patch", and t2 is
  # known to be flaky, while retrying, t1 succeeds but t2 fails again, and the
  # build is expected to be succeed without running "without patch" steps.
  yield api.test(
      'known_flaky_failure_failed_again_while_retrying',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
          tests=['base_unittests']),
      api.chromium_orchestrator.override_compilator_steps(
          tests=['base_unittests']),
      api.chromium_orchestrator.override_compilator_steps(
          with_patch=True, is_compile_phase=False),
      api.properties(**{
          '$build/test_utils': {
              'should_exonerate_flaky_failures': True,
          },
      }),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests', 'with patch', failures=['Test.One', 'Test.Two']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests',
          'retry shards with patch',
          failures=['Test.Two'],
          successes=['Test.One']),
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

  # This test tests the scenario that a known flaky failure shouldn't be retried
  # "without patch". For example: t1, t2, t3 failed "with patch", and t2 is
  # known to be flaky and t3 is known to be deterministically failing.
  # While retrying, t1, t2, t3 fails again, and only t1 is
  # expected to be retried during "without patch".
  yield api.test(
      'without_patch_only_retries_non_flaky_and_not_trunk_failing_failures',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
          tests=['base_unittests']),
      api.chromium_orchestrator.override_compilator_steps(
          tests=['base_unittests']),
      api.chromium_orchestrator.override_compilator_steps(
          with_patch=True, is_compile_phase=False),
      api.chromium_orchestrator.override_compilator_steps(
          with_patch=False, tests=['base_unittests']),
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
  tests = ['base_unittests', 'component_unittests', 'url_unittests']
  yield api.test(
      'summarize_both_retried_and_not_retried_test_suites',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
          tests=tests),
      api.chromium_orchestrator.override_compilator_steps(tests=tests),
      api.chromium_orchestrator.override_compilator_steps(
          with_patch=True, is_compile_phase=False),
      api.chromium_orchestrator.override_compilator_steps(
          with_patch=False, tests=tests),
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
          'url_unittests', 'with patch', failures=['UrlTest.One']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'url_unittests',
          'retry shards with patch',
          failures=['UrlTest.One'],
          successes=['UrlTest.Two']),
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
