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

DEPS = [
    'chromium',
    'chromium_swarming',
    'chromium_tests',
    'chromium_tests_builder_config',
    'findit',
    'recipe_engine/properties',
    'recipe_engine/step',
]

PROPERTIES = InputProperties


# TODO (nqmtuan): Extract out common step for compile and test failures.
def RunSteps(api, properties):
  """Run a specific test for a particular revision."""
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

    step_test, compile_targets = compute_step_test_and_compile_targets(
        api, targets_config, properties.test_to_run)
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
        tests=step_test)

    if compile_result and compile_result.status != common_pb.SUCCESS:
      # TODO (nqmtuan): Send result back to LUCI Bisection.
      return compile_result.status

    # Run tests.
    test_runner = api.chromium_tests.create_test_runner(
        step_test, suffix='bisection')
    with api.chromium_tests.wrap_chromium_tests(
        builder_config, tests=step_test):
      test_result = test_runner()
      # TODO (nqmtuan): Send to LUCI bisection.
      api.step.empty(
          'Send result to LUCI Bisection',
          step_text=(
              'Send to LUCI Bisection with result {}'.format(test_result)))
      return test_result


def compute_step_test_and_compile_targets(api, targets_config, test_to_run):
  """Returns the step test and compile targets.

  The step test will be set with the test filter to run only the test_to_run.
  Note: the step test returned will be in an array, for the purpose of passing
  to other functions down the line.
  """
  for test in targets_config.all_tests:
    if test.canonical_name == test_to_run.test_suite_name:
      # Only runs this test in the test suite.
      test_filter = [test_to_run.test_name]
      test_options = steps.TestOptions.create(test_filter=test_filter)
      test.test_options = test_options

      # Do not run the result handler when running the test.
      # This may prevent errors in the result handlers, for example,
      # when we don't have the permission to upload the result.
      test.spec = attr.evolve(test.spec, results_handler_name=None)
      return [test], test.compile_targets()
  # No tests found.
  api.step.empty(
      'Error: Could not find test',
      status=api.step.FAILURE,
      step_text=('Could not find test {} in test suite {}'.format(
          test_to_run.test_name, test_to_run.test_suite_name)))


def GenTests(api):

  def setup(api,
            target_builder_group='fake-group',
            target_builder='fake-builder'):
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
                'test': 'fake-gtest',
            }],
            'scripts': [{
                'name': 'fake-script-test',
                'script': 'fake-script',
            }],
        },
    }

    t = sum([
        api.chromium.ci_build(
            builder_group=target_builder_group,
            builder=target_builder,
        ),
        api.chromium_tests.read_targets_spec(*_default_spec),
        api.chromium_tests_builder_config.databases(_default_builders),
    ], api.empty_test_data())
    return t

  def setup_input_properties(api,
                             target_builder_group='fake-group',
                             target_builder='fake-builder',
                             test_suite_name='fake-gtest',
                             test_name='gtest-test'):
    props_proto = InputProperties()
    props_proto.target_builder.group = target_builder_group
    props_proto.target_builder.builder = target_builder
    props_proto.test_to_run.test_suite_name = test_suite_name
    props_proto.test_to_run.test_name = test_name
    return sum([api.properties(props_proto)], api.empty_test_data())

  yield api.test(
      'should_run_test',
      setup(api),
      setup_input_properties(api),
      api.post_process(MustRun, 'bot_update'),
      api.post_process(MustRun, 'compile'),
      api.post_process(MustRun, 'fake-gtest (bisection)'),
      api.post_process(DoesNotRun, 'fake-script-test (bisection)'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'compile_failure',
      setup(api),
      setup_input_properties(api),
      api.step_data('compile', retcode=1),
      api.post_process(MustRun, 'bot_update'),
      api.expect_status('FAILURE'),
      api.post_process(MustRun, 'compile'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'Test not found',
      setup(api),
      setup_input_properties(api, test_suite_name='not_found'),
      api.post_process(MustRun, 'Error: Could not find test'),
      api.expect_status('FAILURE'),
      api.post_process(DropExpectation),
  )
