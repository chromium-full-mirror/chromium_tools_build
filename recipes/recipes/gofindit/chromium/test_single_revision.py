# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import attr
from PB.recipes.build.gofindit.chromium.test_single_revision import InputProperties
from recipe_engine import post_process
from recipe_engine.post_process import (DoesNotRun, DropExpectation, MustRun)

from RECIPE_MODULES.build import chromium
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc
from RECIPE_MODULES.build.chromium_tests import steps

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.go.chromium.org.luci.resultdb.proto.v1 import (
    resultdb as resultdb_pb2,
    test_result as test_result_pb2,
)

DEPS = [
    'chromium',
    'chromium_swarming',
    'chromium_tests',
    'chromium_tests_builder_config',
    'findit',
    'gofindit',
    'test_utils',
    'recipe_engine/properties',
    'recipe_engine/resultdb',
    'recipe_engine/step',
]

PROPERTIES = InputProperties


# TODO (nqmtuan): Extract out common step for compile and test failures.
def RunSteps(api, properties):
  """Run tests for a particular revision."""

  target_builder = properties.target_builder
  target_builder_id = chromium.BuilderId.create_for_group(
      target_builder.group, target_builder.builder)

  # If target_builder_id is a tester, this will return the config
  # of the parent builder, which will be used for compile.
  builder_config = api.findit.get_builder_config(target_builder_id)
  builder_id = builder_config.builder_ids[0]
  api.chromium_tests.configure_build(builder_config)

  with api.chromium.chromium_layout():
    update_step, targets_config = api.chromium_tests.prepare_checkout(
        builder_config, set_output_commit=False)
    api.chromium_swarming.configure_swarming('chromium', precommit=False)

    step_tests, compile_targets = compute_step_test_and_compile_targets(
        api, targets_config, properties.tests_to_run)
    api.step.empty(
        'Compute compile targets for test',
        step_text="There are {0} compile targets. Compile targets are {1}."
        .format(len(compile_targets), compile_targets))

    # Compile.
    compile_result, _ = api.chromium_tests.compile_specific_targets(
        builder_id,
        builder_config,
        update_step,
        targets_config,
        compile_targets,
        override_execution_mode=ctbc.COMPILE_AND_TEST,
        tests=step_tests)

    if compile_result and compile_result.status != common_pb.SUCCESS:
      # TODO (nqmtuan): Send result back to LUCI Bisection.
      return compile_result.status

    # Run tests.
    with api.chromium_tests.wrap_chromium_tests(
        builder_config, tests=step_tests):
      api.test_utils.run_tests_once(step_tests, 'bisection')

    test_results = fetch_test_results(api, properties.tests_to_run, step_tests)
    api.gofindit.send_test_results_to_luci_bisection(
        "send_test_results_to_luci_bisection", test_results,
        properties.bisection_host)


def fetch_test_results(api, tests_to_run, step_tests):
  test_ids_by_test_suite = dict()
  for test_to_run in tests_to_run:
    test_ids = test_ids_by_test_suite.setdefault(test_to_run.test_suite_name,
                                                 [])
    test_ids.append(test_to_run.test_id)
  test_results = []
  for test in step_tests:
    test_ids = test_ids_by_test_suite[test.name]
    res = api.resultdb.query_test_results(
        invocations=test.get_invocation_names('bisection'),
        test_id_regexp="({})".format("|".join(test_ids)),
        field_mask_paths=['test_id', 'variant_hash', 'expected', 'status'],
        step_name='query_test_results %s' % test.name,
    )
    test_results.extend(res.test_results)
  return test_results


def compute_step_test_and_compile_targets(api, targets_config, tests_to_run):
  """Returns the step tests and compile targets.

  The step tests will be set with the test filter to run only the tests_to_run.
  """
  test_names_by_test_suite = dict()
  for test_to_run in tests_to_run:
    test_names = test_names_by_test_suite.setdefault(
        test_to_run.test_suite_name, [])
    test_names.append(test_to_run.test_name)

  test_suites = []
  compile_targets = []
  for test in targets_config.all_tests:
    if test.canonical_name in test_names_by_test_suite:
      # Only runs tests presented in tests_to_run.
      test_filter = test_names_by_test_suite[test.canonical_name]
      test_options = steps.TestOptions.create(
          test_filter=test_filter, retry_limit=0)
      test.test_options = test_options

      # Do not run the result handler when running the test.
      # This may prevent errors in the result handlers, for example,
      # when we don't have the permission to upload the result.
      test.spec = attr.evolve(test.spec, results_handler_name=None)
      test_suites.append(test)
      compile_targets.extend(test.compile_targets())
  if not test_suites:
    # No tests found.
    api.step.empty(
        'Error: No test is found',
        status=api.step.FAILURE,
        step_text=('No test is found {}'.format(tests_to_run)))
  return test_suites, compile_targets


def GenTests(api):

  def setup(
      api,
      target_builder_group='fake-group',
      target_builder='fake-builder',
      query_resultdb=False,
  ):
    """Create test properties and other data for tests."""
    _default_builders = ctbc.BuilderDatabase.create({
        target_builder_group: {
            target_builder:
                ctbc.BuilderSpec.create(
                    chromium_config='chromium',
                    gclient_config='chromium',
                ),
        },
    })

    _default_spec = target_builder_group, {
        target_builder: {
            'gtest_tests': [{
                'test': 'fake-gtest'
            }, {
                'test': 'fake-gtest-2'
            }, {
                'test': 'fake-gtest-3'
            }],
            'scripts': [{
                'name': 'fake-script-test',
                'script': 'fake-script',
            }],
        },
    }

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
        ],)
    query = []
    if query_resultdb:
      query.extend([
          api.resultdb.query_test_results(
              query_test_results, step_name="query_test_results fake-gtest"),
          api.resultdb.query_test_results(
              query_test_results, step_name="query_test_results fake-gtest-2")
      ])
    t = sum([
        api.chromium.ci_build(
            builder_group=target_builder_group,
            builder=target_builder,
        ),
        api.chromium_tests.read_targets_spec(*_default_spec),
        api.chromium_tests_builder_config.databases(_default_builders)
    ] + query, api.empty_test_data())
    return t

  def setup_input_properties(
      api,
      tests_to_run,
      target_builder_group='fake-group',
      target_builder='fake-builder',
  ):
    """Set up input properties to test this recipe.

    Attributes:
      * tests_to_run - A list in the form of [(suite_name: test_name), ...].
    """

    props_proto = InputProperties(tests_to_run=[{
        "test_suite_name": test[0],
        "test_name": test[1],
        "test_id": "id_{}".format(test[1])
    } for test in tests_to_run])
    props_proto.target_builder.group = target_builder_group
    props_proto.target_builder.builder = target_builder
    return sum([api.properties(props_proto)], api.empty_test_data())

  yield api.test(
      'should_run_tests',
      setup(api, query_resultdb=True),
      setup_input_properties(
          api,
          tests_to_run=[('fake-gtest', 'gtest-test'),
                        ('fake-gtest-2', 'gtest-test-2')]),
      api.post_process(MustRun, 'bot_update'),
      api.post_process(MustRun, 'compile'),
      api.post_process(MustRun, 'fake-gtest (bisection)'),
      api.post_process(MustRun, 'fake-gtest-2 (bisection)'),
      api.post_process(MustRun, 'send_test_results_to_luci_bisection'),
      api.post_process(DoesNotRun, 'fake-gtest-3 (bisection)'),
      api.post_process(DoesNotRun, 'fake-script-test (bisection)'),
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
      api.post_process(DropExpectation),
  )

  yield api.test(
      'Test not found',
      setup(api),
      setup_input_properties(api, tests_to_run=[('not_found', 'gtest-test')]),
      api.post_process(MustRun, 'Error: No test is found'),
      api.expect_status('FAILURE'),
      api.post_process(DropExpectation),
  )
