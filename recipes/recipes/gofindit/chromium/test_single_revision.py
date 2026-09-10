# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import attr
import re
from PB.recipes.build.gofindit.chromium.test_single_revision import (
  InputProperties,
)
from recipe_engine.post_process import (
  DoesNotRun,
  DropExpectation,
  MustRun,
  LogContains,
)

from RECIPE_MODULES.build import chromium_types
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc
from RECIPE_MODULES.build.chromium_tests import steps

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.go.chromium.org.luci.resultdb.proto.v1 import (
  resultdb as resultdb_pb2,
  test_result as test_result_pb2,
)

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
  chromium,
  chromium_swarming,
  chromium_tests,
  chromium_tests_builder_config,
  findit,
  gofindit,
  test_utils,
)
from RECIPE_MODULES.recipe_engine import properties, resultdb, step


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_swarming: chromium_swarming.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  findit: findit.API
  gofindit: gofindit.API
  properties: properties.API
  resultdb: resultdb.API
  step: step.API
  test_utils: test_utils.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API
  chromium_tests: chromium_tests.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  properties: properties.TEST_API
  resultdb: resultdb.TEST_API
  step: step.TEST_API


PROPERTIES = InputProperties


# TODO (nqmtuan): Extract out common step for compile and test failures.
def RunSteps(api: DEPS, properties):
  """Run tests for a particular revision."""
  test_results = []
  run_succeeded = (
    False  # Whether the build finish running the tests and collecting results.
  )

  try:
    api.chromium_swarming.add_default_tag('is_luci_bisection:true')
    api.chromium_tests.base_variant_getter = lambda spec: {
      'builder': spec.waterfall_buildername,
    }

    target_builder = properties.target_builder
    target_builder_id = chromium_types.BuilderId.create_for_group(
      target_builder.group, target_builder.builder
    )

    # If target_builder_id is a tester, this will return the config
    # of the parent builder, which will be used for compile.
    builder_config = api.findit.get_builder_config(target_builder_id)
    builder_id = builder_config.builder_ids[0]
    api.chromium_tests.configure_build(builder_config)
    if properties.should_clobber:
      api.chromium.c.clobber_before_runhooks = True

    with api.chromium.chromium_layout():
      update_result, build_dir, targets_config = (
        api.chromium_tests.prepare_checkout(
          builder_config, set_output_commit=False
        )
      )
      api.chromium_swarming.configure_swarming(precommit=False)
      checkout_dir = update_result.checkout_dir
      source_dir = update_result.source_root.path

      step_tests, compile_targets = compute_step_test_and_compile_targets(
        api, targets_config, properties.tests_to_run, properties.run_all
      )
      api.step.empty(
        'Compute compile targets for test',
        step_text="There are {0} compile targets. Compile targets are {1}.".format(
          len(compile_targets), compile_targets
        ),
      )

      # Compile.
      compile_result, compile_output = (
        api.chromium_tests.compile_specific_targets(
          build_dir,
          builder_id,
          builder_config,
          update_result,
          targets_config,
          compile_targets,
          override_execution_mode=ctbc.COMPILE_AND_TEST,
          tests=step_tests,
        )
      )

      if compile_result and compile_result.status != common_pb.SUCCESS:
        return compile_result.status

      if compile_output:
        api.chromium_tests.isolate_test_targets(
          source_dir, build_dir, builder_config, update_result, compile_output
        )

      # Run tests.
      # If we have < 10 tests to runs, trigger fast runs, which
      # gives the swarming task a higher priority.
      # Perhaps it worths it to check for only tests that actually exist,
      # but we are not doing it now, given that we rarely run more than
      # 10 tests, and most tests should exist anyway.
      if not properties.run_all and len(properties.tests_to_run) < 10:
        api.step.empty('adjust_fast_run_priority')
        api.chromium_swarming.default_priority -= 1

      suffix = 'bisection'
      with api.chromium_tests.wrap_chromium_tests(
        checkout_dir, source_dir, build_dir, tests=step_tests
      ):
        api.test_utils.run_tests_once(
          checkout_dir, source_dir, build_dir, step_tests, suffix
        )
      test_results = fetch_test_results(
        api, properties.tests_to_run, step_tests, suffix
      )
      run_succeeded = True
  finally:
    api.gofindit.send_test_results_to_luci_bisection(
      "send_test_results_to_luci_bisection",
      test_results,
      run_succeeded,
      properties.bisection_host,
    )


def fetch_test_results(api: DEPS, tests_to_run, step_tests, suffix):
  test_ids_by_test_suite = {}
  for test_to_run in tests_to_run:
    test_ids = test_ids_by_test_suite.setdefault(
      test_to_run.test_suite_name, []
    )
    test_ids.append(test_to_run.test_id)
  test_results = []
  for test in step_tests:
    test_ids = test_ids_by_test_suite[test.canonical_name]
    res = api.resultdb.query_test_results(
      invocations=test.get_invocation_names(suffix),
      test_id_regexp="({})".format(
        "|".join([re.escape(id) for id in test_ids])
      ),
      field_mask_paths=['test_id', 'variant_hash', 'expected', 'status'],
      step_name='query_test_results %s' % test.canonical_name,
    )
    test_results.extend(res.test_results)
  return test_results


def compute_step_test_and_compile_targets(
  api: DEPS, targets_config, tests_to_run, run_all
):
  """Returns the step tests and compile targets.

  The step tests will be set with the test filter to run only the tests_to_run.
  """
  test_names_by_test_suite = {}
  for test_to_run in tests_to_run:
    test_names = test_names_by_test_suite.setdefault(
      test_to_run.test_suite_name, []
    )
    test_names.append(test_to_run.test_name)

  test_suites = []
  compile_targets = []
  for test in targets_config.all_tests:
    if test.canonical_name in test_names_by_test_suite:
      # Only runs tests presented in tests_to_run.
      test_options = steps.TestOptions.create(retry_limit=0, run_disabled=True)
      if not run_all:
        test_filter = test_names_by_test_suite[test.canonical_name]
        test_options = attr.evolve(test_options, test_filter=test_filter)
        # Customise the number of shards only when the number of tests to run is less than 100.
        # Otherwise, use the default number of shards. This is to avoid creating too many shards.
        if len(test_filter) < 100:
          nshards = len(test_filter) // 10 + 1
          test.spec = attr.evolve(test.spec, shards=nshards)
      test.test_options = test_options

      resultdb = test.spec.resultdb
      resultdb = attr.evolve(
        resultdb, base_tags=(('is_luci_bisection', 'true'),)
      )
      # Do not run the result handler when running the test.
      # This may prevent errors in the result handlers, for example,
      # when we don't have the permission to upload the result.
      test.spec = attr.evolve(
        test.spec, results_handler_name=None, resultdb=resultdb
      )
      test_suites.append(test)
      compile_targets.extend(test.compile_targets())
  if not test_suites:
    # No tests found.
    api.step.empty(
      'Error: No test is found',
      status=api.step.FAILURE,
      step_text=('No test is found {}'.format(tests_to_run)),
    )
  return test_suites, compile_targets


def GenTests(api: TEST_DEPS):

  def setup(
    api: TEST_DEPS,
    target_builder_group='fake-group',
    target_builder='fake-builder',
    query_resultdb=False,
  ):
    """Create test properties and other data for tests."""
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
    t = sum(
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
    return t

  def setup_input_properties(
    api: TEST_DEPS,
    tests_to_run,
    target_builder_group='fake-group',
    target_builder='fake-builder',
    should_clobber=False,
    run_all=False,
  ):
    """Set up input properties to test this recipe.

    Attributes:
      * tests_to_run - A list in the form of [(suite_name: test_name), ...].
    """

    props_proto = InputProperties(
      tests_to_run=[
        {
          "test_suite_name": test[0],
          "test_name": test[1],
          "test_id": "id_{}".format(test[1]),
        }
        for test in tests_to_run
      ]
    )
    props_proto.target_builder.group = target_builder_group
    props_proto.target_builder.builder = target_builder
    props_proto.should_clobber = should_clobber
    props_proto.run_all = run_all
    return sum([api.properties(props_proto)], api.empty_test_data())

  yield api.test(
    'should_run_tests',
    setup(api, query_resultdb=True),
    setup_input_properties(
      api,
      tests_to_run=[
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
    setup_input_properties(
      api,
      tests_to_run=[
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
    setup_input_properties(
      api,
      tests_to_run=[
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
    setup_input_properties(
      api,
      tests_to_run=[
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
    setup_input_properties(api, tests_to_run=[('fake-gtest', 'gtest-test')]),
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
    setup_input_properties(api, tests_to_run=[('not_found', 'gtest-test')]),
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
