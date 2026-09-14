# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from dataclasses import dataclass

from PB.go.chromium.org.luci.resultdb.proto.v1 import (
  resultdb as resultdb_pb2,
  test_result as test_result_pb2,
)
from recipe_engine.post_process import (
  DoesNotRun,
  DropExpectation,
  LogContains,
  MustRun,
)
from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc
from RECIPE_MODULES.build import (
  chromium,
  chromium_tests,
  chromium_tests_builder_config,
  gofindit,
)
from RECIPE_MODULES.recipe_engine import properties, resultdb, step


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  gofindit: gofindit.API
  properties: properties.API
  resultdb: resultdb.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API
  chromium_tests: chromium_tests.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  properties: properties.TEST_API
  resultdb: resultdb.TEST_API
  step: step.TEST_API


@dataclass
class Test:
  test_suite_name: str
  test_name: str
  test_id: str


def RunSteps(api: DEPS):
  builder_id, builder_config = (
    api.chromium_tests_builder_config.lookup_builder()
  )
  raw_tests = api.properties.get('tests') or api.properties.get(
    'tests_to_run', []
  )
  tests = [
    Test(
      test_suite_name=t['test_suite_name'],
      test_name=t['test_name'],
      test_id=t['test_id'],
    )
    for t in raw_tests
  ]
  should_clobber = api.properties.get('should_clobber', False)
  run_all = api.properties.get('run_all', False)
  bisection_host = api.properties.get(
    'bisection_host', 'luci-bisection.appspot.com'
  )

  return api.gofindit.test_single_revision_steps(
    builder_id=builder_id,
    builder_config=builder_config,
    tests=tests,
    bisection_host=bisection_host,
    should_clobber=should_clobber,
    run_all=run_all,
  )


def GenTests(api: TEST_DEPS):

  def setup(
    api: TEST_DEPS,
    target_builder_group='fake-group',
    target_builder='fake-builder',
    query_resultdb=False,
  ):
    _default_builders = ctbc.BuilderDatabase.create(
      {
        target_builder_group: {
          target_builder: ctbc.BuilderSpec.create(
            chromium_config='chromium',
            gclient_config='chromium',
          ),
        },
      }
    )

    _default_spec = (
      target_builder_group,
      {
        target_builder: {
          'gtest_tests': [
            {
              'test': 'fake-gtest',
              'swarming': {
                'dimensions': {
                  'os': 'Mac',
                },
              },
            },
            {
              'test': 'fake-gtest-2',
              'swarming': {
                'dimensions': {
                  'os': 'Mac',
                },
              },
            },
            {
              'test': 'fake-gtest-3',
              'swarming': {
                'dimensions': {
                  'os': 'Mac',
                },
              },
            },
          ],
          'scripts': [
            {
              'name': 'fake-script-test',
              'script': 'fake-script',
            }
          ],
        },
      },
    )

    query_test_results = resultdb_pb2.QueryTestResultsResponse(
      test_results=[
        test_result_pb2.TestResult(
          test_id='gtest-test-2',
          variant_hash="123",
          expected=False,
          status=test_result_pb2.PASS,
        ),
        test_result_pb2.TestResult(
          test_id='gtest-test',
          variant_hash="123",
          expected=False,
          status=test_result_pb2.PASS,
        ),
      ],
    )
    query = []
    if query_resultdb:
      query.extend(
        [
          api.resultdb.query_test_results(
            query_test_results, step_name="query_test_results fake-gtest"
          ),
          api.resultdb.query_test_results(
            query_test_results, step_name="query_test_results fake-gtest-2"
          ),
        ]
      )
    return sum(
      [
        api.chromium.ci_build(
          builder_group=target_builder_group,
          builder=target_builder,
        ),
        api.chromium_tests.read_targets_spec(*_default_spec),
        api.chromium_tests_builder_config.databases(_default_builders),
      ]
      + query,
      api.empty_test_data(),
    )

  def setup_props(
    api: TEST_DEPS,
    tests,
    should_clobber=False,
    run_all=False,
  ):
    props = {
      'tests': [
        {
          'test_suite_name': test[0],
          'test_name': test[1],
          'test_id': f'id_{test[1]}',
        }
        for test in tests
      ],
      'should_clobber': should_clobber,
      'run_all': run_all,
    }
    return api.properties(**props)

  yield api.test(
    'should_run_tests',
    setup(api, query_resultdb=True),
    setup_props(
      api,
      tests=[
        ('fake-gtest', 'gtest-test'),
        ('fake-gtest-2', 'gtest-test-2'),
      ],
    ),
    api.post_process(MustRun, 'bot_update'),
    api.post_process(MustRun, 'adjust_fast_run_priority'),
    api.post_process(MustRun, 'compile'),
    api.post_process(MustRun, 'fake-gtest (bisection) on Mac'),
    api.post_process(MustRun, 'fake-gtest-2 (bisection) on Mac'),
    api.post_process(MustRun, 'send_test_results_to_luci_bisection'),
    api.post_process(
      LogContains,
      "send_test_results_to_luci_bisection",
      "input",
      ['"runSucceeded": true'],
    ),
    api.post_process(DoesNotRun, 'fake-gtest-3 (bisection) on Mac'),
    api.post_process(DoesNotRun, 'fake-script-test (bisection)'),
    api.post_process(DropExpectation),
  )

  yield api.test(
    'run_more_than_9_tests',
    setup(api, query_resultdb=True),
    setup_props(
      api,
      tests=[
        ('fake-gtest', 'gtest-test'),
        ('fake-gtest-2', 'gtest-test-2'),
        ('fake-gtest-3-1', 'gtest-test-3'),
        ('fake-gtest-3-2', 'gtest-test-3'),
        ('fake-gtest-3-3', 'gtest-test-3'),
        ('fake-gtest-3-4', 'gtest-test-3'),
        ('fake-gtest-3-5', 'gtest-test-3'),
        ('fake-gtest-3-6', 'gtest-test-3'),
        ('fake-gtest-3-7', 'gtest-test-3'),
        ('fake-gtest-3-8', 'gtest-test-3'),
      ],
    ),
    api.post_process(MustRun, 'bot_update'),
    api.post_process(MustRun, 'compile'),
    api.post_process(MustRun, 'fake-gtest (bisection) on Mac'),
    api.post_process(MustRun, 'fake-gtest-2 (bisection) on Mac'),
    api.post_process(MustRun, 'send_test_results_to_luci_bisection'),
    api.post_process(
      LogContains,
      "send_test_results_to_luci_bisection",
      "input",
      ['"runSucceeded": true'],
    ),
    api.post_process(DoesNotRun, 'adjust_fast_run_priority'),
    api.post_process(DropExpectation),
  )

  yield api.test(
    'full_run',
    setup(api, query_resultdb=True),
    setup_props(
      api,
      tests=[
        ('fake-gtest', 'gtest-test'),
        ('fake-gtest-2', 'gtest-test-2'),
      ],
      run_all=True,
    ),
    api.post_process(MustRun, 'bot_update'),
    api.post_process(MustRun, 'compile'),
    api.post_process(MustRun, 'fake-gtest (bisection) on Mac'),
    api.post_process(MustRun, 'fake-gtest-2 (bisection) on Mac'),
    api.post_process(MustRun, 'send_test_results_to_luci_bisection'),
    api.post_process(
      LogContains,
      "send_test_results_to_luci_bisection",
      "input",
      ['"runSucceeded": true'],
    ),
    api.post_process(DoesNotRun, 'adjust_fast_run_priority'),
    api.post_process(DropExpectation),
  )

  yield api.test(
    'run_tests_with_clobber',
    setup(api, query_resultdb=True),
    setup_props(
      api,
      tests=[
        ('fake-gtest', 'gtest-test'),
        ('fake-gtest-2', 'gtest-test-2'),
      ],
      should_clobber=True,
    ),
    api.post_process(MustRun, 'bot_update'),
    api.post_process(MustRun, 'clobber'),
    api.post_process(MustRun, 'compile'),
    api.post_process(MustRun, 'fake-gtest (bisection) on Mac'),
    api.post_process(
      LogContains,
      "send_test_results_to_luci_bisection",
      "input",
      ['"runSucceeded": true'],
    ),
    api.post_process(DropExpectation),
  )

  yield api.test(
    'compile_failure',
    setup(api),
    setup_props(api, tests=[('fake-gtest', 'gtest-test')]),
    api.step_data('compile', retcode=1),
    api.post_process(MustRun, 'bot_update'),
    api.expect_status('FAILURE'),
    api.post_process(MustRun, 'compile'),
    api.post_process(
      LogContains,
      "send_test_results_to_luci_bisection",
      "input",
      ['"runSucceeded": false'],
    ),
    api.post_process(DropExpectation),
  )

  yield api.test(
    'test_not_found',
    setup(api),
    setup_props(api, tests=[('not_found', 'gtest-test')]),
    api.post_process(MustRun, 'Error: No test is found'),
    api.expect_status('FAILURE'),
    api.post_process(
      LogContains,
      "send_test_results_to_luci_bisection",
      "input",
      ['"runSucceeded": false'],
    ),
    api.post_process(DropExpectation),
  )
