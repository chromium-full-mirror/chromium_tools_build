# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium, chromium_tests, test_utils
from RECIPE_MODULES.recipe_engine import (
  assertions,
  json,
  luci_analysis,
  path,
  properties,
  raw_io,
  step,
  time,
)


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  chromium: chromium.API
  chromium_tests: chromium_tests.API
  json: json.API
  luci_analysis: luci_analysis.API
  path: path.API
  properties: properties.API
  raw_io: raw_io.API
  step: step.API
  test_utils: test_utils.API
  time: time.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API
  json: json.TEST_API
  luci_analysis: luci_analysis.TEST_API
  properties: properties.TEST_API
  raw_io: raw_io.TEST_API
  test_utils: test_utils.TEST_API


from google.protobuf import json_format
from google.protobuf import timestamp_pb2
from recipe_engine.recipe_api import Property
from recipe_engine import post_process

from RECIPE_MODULES.build.chromium_tests import steps

PROPERTIES = {
  # This property is a dictionary that specifies the expectations of the
  # labeled known flakes of the mocked tests, and the format is from a test
  # name to a list of tests.
  'known_luci_analysis_flakes_expectations': Property(default={}),
  'weak_flaky_failures': Property(default={}),
  # This property is a boolean that indicates whether to create a mocked test
  # that has failed tests to test that if there are no test failures, a
  # request should NOT be sent to the service because it's unnecessary.
  'exclude_failed_test': Property(default=False),
  # This property is a boolean that indicates whether to create a mocked test
  # that has a massive amount of failures to test that a request should NOT be
  # sent to the service to avoid overloading it.
  'has_too_many_failures': Property(default=False),
  # This property indicates how many failed_test suites to create
  'failed_test_count': Property(default=1),
  # This property indicates whether all the mock tests will be valid
  'all_valid': Property(default=False),
}


def RunSteps(
  api: DEPS,
  known_luci_analysis_flakes_expectations,
  weak_flaky_failures,
  exclude_failed_test,
  has_too_many_failures,
  all_valid,
):
  test_specs = [
    steps.MockTestSpec.create(name='succeeded_test'),
    steps.MockTestSpec.create(
      name='invalid_test', runs_on_swarming=True, has_valid_results=all_valid
    ),
  ]

  if not exclude_failed_test:
    test_suite_name = 'failed_test'
    per_suffix_failures = {'with patch': ['testA', 'testB']}
    test_specs.append(
      steps.MockTestSpec.create(
        name=test_suite_name,
        runs_on_swarming=True,
        per_suffix_failures=per_suffix_failures,
        invocation_names=['invocations/whatever'],
      )
    )

  if has_too_many_failures:
    test_specs.append(
      steps.MockTestSpec.create(
        name='too_many_failures',
        runs_on_swarming=True,
        per_suffix_failures={'with patch': ['test%d' % i for i in range(1000)]},
      )
    )
  tests = [s.get_test(api.chromium_tests) for s in test_specs]

  checkout_dir = api.path.start_dir
  source_dir = checkout_dir / 'fake-repo'
  build_dir = source_dir / 'out' / 'some_build_dir'
  api.test_utils.run_tests(
    checkout_dir,
    source_dir,
    build_dir,
    tests,
    'with patch',
    retry_failed_shards=True,
    retry_invalid_shards=True,
  )

  for t in tests:
    api.assertions.assertEqual(
      t.known_luci_analysis_flaky_failures,
      set(known_luci_analysis_flakes_expectations.get(t.name, [])),
    )
    api.assertions.assertEqual(
      t.weak_luci_analysis_flaky_failures,
      set(weak_flaky_failures.get(t.name, [])),
    )


def GenTests(api: TEST_DEPS):

  yield api.test(
    'immune to infra failure of querying flaky failures',
    api.chromium.generic_build(
      builder_group='fake-group',
      builder='fake-builder',
    ),
    api.properties(
      **{
        '$build/test_utils': {
          'should_exonerate_flaky_failures': True,
        },
      }
    ),
    api.override_step_data(
      'query LUCI Analysis for stability.rpc call', retcode=1
    ),
    api.post_process(
      post_process.MustRun, 'error querying LUCI Analysis for failure rates'
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'immune to ill-formed response',
    api.chromium.generic_build(
      builder_group='fake-group',
      builder='fake-builder',
    ),
    api.properties(
      **{
        '$build/test_utils': {
          'should_exonerate_flaky_failures': True,
        },
      }
    ),
    api.step_data(
      'query LUCI Analysis for stability.rpc call',
      stdout=api.json.output({'testVariants': []}),
    ),
    api.post_process(
      post_process.MustRun, 'error querying LUCI Analysis for failure rates'
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'empty response',
    api.chromium.generic_build(
      builder_group='fake-group',
      builder='fake-builder',
    ),
    api.properties(
      **{
        '$build/test_utils': {
          'should_exonerate_flaky_failures': True,
        },
      }
    ),
    api.step_data(
      'query LUCI Analysis for stability.rpc call',
      stdout=api.json.output({}),
    ),
    api.post_process(
      post_process.MustRun, 'error querying LUCI Analysis for failure rates'
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'no failed tests',
    api.chromium.generic_build(
      builder_group='fake-group',
      builder='fake-builder',
    ),
    api.properties(
      exclude_failed_test=True,
      **{
        '$build/test_utils': {
          'should_exonerate_flaky_failures': True,
        },
      },
    ),
    api.post_process(
      post_process.DoesNotRun, 'query LUCI Analysis for stability'
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'no tests are marked as known flaky or recently failing',
    api.chromium.generic_build(
      builder_group='fake-group',
      builder='fake-builder',
    ),
    api.override_step_data(
      'collect tasks (with patch).failed_test results',
      stdout=api.raw_io.output_text(
        api.test_utils.rdb_results(
          'failed_test', failing_tests=['testA', 'testB']
        )
      ),
    ),
    api.properties(
      known_flakes_expectations={},
      **{
        '$build/test_utils': {
          'should_exonerate_flaky_failures': True,
        },
      },
    ),
    api.override_step_data(
      'query LUCI Analysis for stability.rpc call',
      stdout=api.json.output(
        api.luci_analysis.generate_stability_response(
          [
            api.luci_analysis.generate_stability_analysis(
              test_id='ninja://failed_test/testA'
            ),
            api.luci_analysis.generate_stability_analysis(
              test_id='ninja://failed_test/testB'
            ),
          ]
        )
      ),
    ),
    api.post_process(post_process.DropExpectation),
  )

  def CheckStepInput(check, step_odict, step_name, test_name):
    step = step_odict[step_name]
    check(test_name in step.stdin)

  yield api.test(
    'all tests were skipped',
    api.chromium.generic_build(
      builder_group='fake-group',
      builder='fake-builder',
    ),
    api.properties(
      known_luci_analysis_flakes_expectations={},
      weak_flaky_failures={},
      **{
        '$build/test_utils': {
          'should_exonerate_flaky_failures': True,
        },
      },
    ),
    api.override_step_data(
      'collect tasks (with patch).failed_test results',
      stdout=api.raw_io.output_text(
        api.test_utils.rdb_results('failed_test', skipped_tests=['testA'])
      ),
    ),
    api.luci_analysis.query_failure_rate_results(
      [
        api.luci_analysis.generate_analysis(
          test_id='ninja://failed_test/testA', flaky_verdict_counts=[10]
        ),
      ]
    ),
    api.override_step_data(
      'query LUCI Analysis for stability.rpc call',
      stdout=api.json.output(
        api.luci_analysis.generate_stability_response(
          [
            api.luci_analysis.generate_stability_analysis(
              test_id='ninja://failed_test/testA',
              failure_rate_is_met=True,
            ),
          ]
        )
      ),
    ),
    api.post_process(
      post_process.DoesNotRun, 'exonerate unrelated test failures'
    ),
    api.post_process(
      post_process.MustRun, 'failed_test (retry shards with patch)'
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'all of the tests are marked as known flaky',
    api.chromium.generic_build(
      builder_group='fake-group',
      builder='fake-builder',
    ),
    api.override_step_data(
      'collect tasks (with patch).failed_test results',
      stdout=api.raw_io.output_text(
        api.test_utils.rdb_results(
          'failed_test', failing_tests=['testA', 'testB']
        )
      ),
    ),
    api.properties(
      known_luci_analysis_flakes_expectations={
        'failed_test': ['testA', 'testB'],
      },
      **{
        '$build/test_utils': {
          'should_exonerate_flaky_failures': True,
        },
      },
    ),
    api.override_step_data(
      'query LUCI Analysis for stability.rpc call',
      stdout=api.json.output(
        api.luci_analysis.generate_stability_response(
          [
            api.luci_analysis.generate_stability_analysis(
              test_id='ninja://failed_test/testA',
              failure_rate_is_met=False,
              flake_rate_is_met=True,
              run_flaky_verdicts_1wd=1,
              run_flaky_verdicts_12h=1,
            ),
            api.luci_analysis.generate_stability_analysis(
              test_id='ninja://failed_test/testB',
              failure_rate_is_met=False,
              flake_rate_is_met=True,
              run_flaky_verdicts_1wd=1,
              run_flaky_verdicts_12h=1,
            ),
          ]
        )
      ),
    ),
    api.post_process(
      CheckStepInput, 'exonerate unrelated test failures', 'testA'
    ),
    api.post_process(
      CheckStepInput, 'exonerate unrelated test failures', 'testB'
    ),
    # Ensure LUCI Analysis is in the explaination
    api.post_process(
      CheckStepInput, 'exonerate unrelated test failures', 'LUCI Analysis'
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'tests are deterministically failing',
    api.chromium.generic_build(
      builder_group='fake-group',
      builder='fake-builder',
    ),
    api.override_step_data(
      'collect tasks (with patch).failed_test results',
      stdout=api.raw_io.output_text(
        api.test_utils.rdb_results('failed_test', failing_tests=['testA'])
      ),
    ),
    api.override_step_data(
      'collect tasks (with patch).failed_test results',
      stdout=api.raw_io.output_text(
        api.test_utils.rdb_results('failed_test', failing_tests=['testA'])
      ),
    ),
    api.properties(
      known_luci_analysis_flakes_expectations={
        'failed_test': ['testA'],
      },
      **{
        '$build/test_utils': {
          'should_exonerate_flaky_failures': True,
        },
      },
    ),
    api.override_step_data(
      'query LUCI Analysis for stability.rpc call',
      stdout=api.json.output(
        api.luci_analysis.generate_stability_response(
          [
            api.luci_analysis.generate_stability_analysis(
              test_id='ninja://failed_test/testA',
              failure_rate_is_met=True,
            ),
          ]
        )
      ),
    ),
    api.post_process(post_process.PropertiesContain, 'luci_analysis_info'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'skip querying if there are too many failures',
    api.chromium.generic_build(
      builder_group='fake-group',
      builder='fake-builder',
    ),
    api.properties(
      exclude_failed_test=True,
      has_too_many_failures=True,
      **{
        '$build/test_utils': {
          'should_exonerate_flaky_failures': True,
        },
      },
    ),
    api.post_process(
      post_process.DoesNotRun, 'query LUCI Analysis for stability'
    ),
    api.post_process(post_process.DropExpectation),
  )

  # The difference between this test and the immediate above one is that this
  # test doesn't exclude the test suite with limited number of failures, and
  # this test tests that even though there are tests with too many failures, the
  # recipe should still query known flaky failures for other test suites with
  # limited number of failures.
  yield api.test(
    'keep querying if at least one test suite has limited failures',
    api.chromium.generic_build(
      builder_group='fake-group',
      builder='fake-builder',
    ),
    api.override_step_data(
      'collect tasks (with patch).failed_test results',
      stdout=api.raw_io.output_text(
        api.test_utils.rdb_results(
          'failed_test', failing_tests=['testA', 'testB']
        )
      ),
    ),
    api.properties(
      has_too_many_failures=True,
      known_luci_analysis_flakes_expectations={
        'failed_test': ['testA', 'testB'],
      },
      **{
        '$build/test_utils': {
          'should_exonerate_flaky_failures': True,
        },
      },
    ),
    api.override_step_data(
      'query LUCI Analysis for stability.rpc call',
      stdout=api.json.output(
        api.luci_analysis.generate_stability_response(
          [
            api.luci_analysis.generate_stability_analysis(
              test_id='ninja://failed_test/testA',
              failure_rate_is_met=True,
            ),
            api.luci_analysis.generate_stability_analysis(
              test_id='ninja://failed_test/testB',
              failure_rate_is_met=True,
            ),
          ]
        )
      ),
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'retry_weak_weetbix_exonerations does not run strongly exonerated',
    api.chromium.generic_build(
      builder_group='fake-group',
      builder='fake-builder',
    ),
    api.properties(
      known_luci_analysis_flakes_expectations={
        'failed_test': ['testA', 'testB'],
      },
      **{
        '$build/test_utils': {
          'should_exonerate_flaky_failures': True,
        },
      },
    ),
    api.override_step_data(
      'collect tasks (with patch).failed_test results',
      stdout=api.raw_io.output_text(
        api.test_utils.rdb_results(
          'failed_test', failing_tests=['testA', 'testB']
        )
      ),
    ),
    api.override_step_data(
      'query LUCI Analysis for stability.rpc call',
      stdout=api.json.output(
        api.luci_analysis.generate_stability_response(
          [
            api.luci_analysis.generate_stability_analysis(
              test_id='ninja://failed_test/testA',
              failure_rate_is_met=True,
              flake_rate_is_met=True,
              run_flaky_verdicts_1wd=1,
              run_flaky_verdicts_12h=1,
            ),
            api.luci_analysis.generate_stability_analysis(
              test_id='ninja://failed_test/testB',
              failure_rate_is_met=True,
              flake_rate_is_met=True,
              run_flaky_verdicts_1wd=1,
              run_flaky_verdicts_12h=1,
            ),
          ]
        )
      ),
    ),
    api.post_process(post_process.MustRun, 'failed_test (with patch)'),
    api.post_process(
      post_process.DoesNotRun, 'failed_test (retry shards with patch)'
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'retry_weak_weetbix_exonerations does run weakly exonerated',
    api.chromium.generic_build(
      builder_group='fake-group',
      builder='fake-builder',
    ),
    api.properties(
      known_luci_analysis_flakes_expectations={
        'failed_test': ['testA', 'testB'],
      },
      weak_flaky_failures={
        'failed_test': ['testA', 'testB'],
      },
      # Need to make sure the retry step isn't because of an invalid test
      all_valid=True,
      **{
        '$build/test_utils': {
          'should_exonerate_flaky_failures': True,
        },
      },
    ),
    api.override_step_data(
      'collect tasks (with patch).failed_test results',
      stdout=api.raw_io.output_text(
        api.test_utils.rdb_results(
          'failed_test', failing_tests=['testA', 'testB']
        )
      ),
    ),
    api.override_step_data(
      'query LUCI Analysis for stability.rpc call',
      stdout=api.json.output(
        api.luci_analysis.generate_stability_response(
          [
            api.luci_analysis.generate_stability_analysis(
              test_id='ninja://failed_test/testA',
              failure_rate_is_met=False,
              flake_rate_is_met=True,
              run_flaky_verdicts_1wd=1,
              run_flaky_verdicts_12h=0,
            ),
            api.luci_analysis.generate_stability_analysis(
              test_id='ninja://failed_test/testB',
              failure_rate_is_met=False,
              flake_rate_is_met=True,
              run_flaky_verdicts_1wd=1,
              run_flaky_verdicts_12h=0,
            ),
          ]
        )
      ),
    ),
    api.post_process(post_process.MustRun, 'failed_test (with patch)'),
    api.post_process(
      post_process.MustRun, 'failed_test (retry shards with patch)'
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'does not exonerate over 100',
    api.chromium.generic_build(
      builder_group='fake-group',
      builder='fake-builder',
    ),
    api.properties(
      known_luci_analysis_flakes_expectations={
        'failed_test': [],
      },
      **{
        '$build/test_utils': {
          'should_exonerate_flaky_failures': True,
        },
      },
    ),
    api.override_step_data(
      'collect tasks (with patch).failed_test results',
      stdout=api.raw_io.output_text(
        api.test_utils.rdb_results(
          'failed_test', failing_tests=['test%d' % t for t in range(101)]
        )
      ),
    ),
    api.post_process(
      post_process.DoesNotRun, 'query LUCI Analysis for stability.rpc call'
    ),
    api.post_process(
      post_process.StepTextContains,
      'Skipping querying LUCI Analysis for failure rates',
      ['101'],
    ),
    api.post_process(post_process.DropExpectation),
  )
