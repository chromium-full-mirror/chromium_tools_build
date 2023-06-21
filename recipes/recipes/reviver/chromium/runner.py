# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Recipe for a polymorphic runner for Test Reviver.

The builder using this recipe should be triggered via another builder
with properties set that are returned from
chromium_polymorphic.get_target_properties(...). This will build and run
tests for the target builder, running the disabled test cases. Tests
that don't support running disabled tests will not be run.

The test results will have the reviver_project, reviver_bucket and
reviver_builder variants set to the project, bucket and name of the
target builder.
"""

import itertools

from recipe_engine import post_process

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc
from RECIPE_MODULES.build.chromium_tests import steps

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb

PYTHON_VERSION_COMPATIBILITY = 'PY3'

DEPS = [
    'chromium',
    'chromium_polymorphic',
    'chromium_swarming',
    'chromium_tests',
    'chromium_tests_builder_config',
    'code_coverage',
    'test_utils',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
]


def RunSteps(api):
  builder_id, builder_config = api.chromium_polymorphic.lookup_builder_config()

  # Set the ResultDB variants so that tests for the target builder can be queried
  target_builder = api.chromium_polymorphic.target_builder_id
  api.chromium_tests.base_variant_getter = lambda spec: {
      'reviver_project': target_builder.project,
      'reviver_bucket': target_builder.bucket,
      'reviver_builder': spec.waterfall_buildername,
  }

  api.chromium_tests.configure_build(builder_config)

  with api.chromium.chromium_layout():
    update_step, targets_config = api.chromium_tests.prepare_checkout(
        builder_config, report_cache_state=False)
    api.chromium_swarming.configure_swarming(
        'chromium', precommit=False, builder_group=builder_id.group)

    # Set the test options so that the tests run disabled test cases, skipping
    # tests that don't support it
    test_options = steps.TestOptions(run_disabled=True)
    tests = []
    for t in targets_config.all_tests:
      if t.option_flags.run_disabled_flag:
        t.test_options = test_options
        tests.append(t)

    # Only compile targets for the tests that will be run
    compile_targets = sorted(
        set(itertools.chain.from_iterable(t.compile_targets() for t in tests)))

    compile_result, _ = api.chromium_tests.compile_specific_targets(
        builder_id, builder_config, update_step, targets_config,
        compile_targets, tests)
    if compile_result and compile_result.status != common_pb.SUCCESS:
      return compile_result

    # There will pretty much always be test failures since it is unlikely that
    # all disabled tests can be re-enabled, so don't fail the build due to
    # failed tests
    test_runner = api.chromium_tests.create_test_runner(
        tests,
        serialize_tests=builder_config.serialize_tests,
        enable_infra_failure=True)
    with api.chromium_tests.wrap_chromium_tests(builder_config, tests=tests):
      test_result = test_runner()
      if api.code_coverage.using_coverage:
        api.code_coverage.process_coverage_data(tests)
      if (test_result and
          test_result.status not in (common_pb.SUCCESS, common_pb.FAILURE)):
        return test_result


def GenTests(api):
  ctbc_api = api.chromium_tests_builder_config

  yield api.test(
      'basic',
      api.chromium.generic_build(
          project='reviver-project',
          bucket='reviver-bucket',
          builder='fake-runner',
          builder_group=None,
      ),
      api.chromium_polymorphic.triggered_properties(
          project='fake-project',
          bucket='fake-bucket',
          builder='fake-builder',
          builder_group='fake-group',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder='fake-builder',
              builder_group='fake-group',
          ).assemble()),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'fake-gtest',
                  }],
                  'scripts': [{
                      'name': 'fake-script-test',
                      'script': 'fake-script',
                  }],
              },
          }),
      api.post_check(post_process.StepCommandContains, 'compile',
                     ['fake-gtest']),
      api.post_check(post_process.StepCommandContains, 'fake-gtest',
                     ['--gtest_also_run_disabled_tests']),
      api.post_check(post_process.StepCommandContains, 'fake-gtest',
                     ['-var', 'reviver_project:fake-project']),
      api.post_check(post_process.StepCommandContains, 'fake-gtest',
                     ['-var', 'reviver_bucket:fake-bucket']),
      api.post_check(post_process.StepCommandContains, 'fake-gtest',
                     ['-var', 'reviver_builder:fake-builder']),
      api.post_check(post_process.DoesNotRun, 'fake-script-test'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'legacy-builder-config',
      api.chromium.generic_build(
          project='reviver-project',
          bucket='reviver-bucket',
          builder='fake-runner',
          builder_group=None,
      ),
      api.chromium_polymorphic.triggered_properties(
          project='fake-project',
          bucket='fake-bucket',
          builder='fake-builder',
          builder_group='fake-group',
      ),
      ctbc_api.databases(
          builder_db=ctbc.BuilderDatabase.create({
              'fake-group': {
                  'fake-builder':
                      ctbc.BuilderSpec.create(
                          gclient_config='chromium',
                          chromium_config='chromium',
                      ),
              },
          })),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'fake-gtest',
                  }],
                  'scripts': [{
                      'name': 'fake-script-test',
                      'script': 'fake-script',
                  }],
              },
          }),
      api.post_check(post_process.StepCommandContains, 'compile',
                     ['fake-gtest']),
      api.post_check(post_process.StepCommandContains, 'fake-gtest',
                     ['--gtest_also_run_disabled_tests']),
      api.post_check(post_process.StepCommandContains, 'fake-gtest',
                     ['-var', 'reviver_project:fake-project']),
      api.post_check(post_process.StepCommandContains, 'fake-gtest',
                     ['-var', 'reviver_bucket:fake-bucket']),
      api.post_check(post_process.StepCommandContains, 'fake-gtest',
                     ['-var', 'reviver_builder:fake-builder']),
      api.post_check(post_process.DoesNotRun, 'fake-script-test'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'compile-failure',
      api.chromium.generic_build(
          project='reviver-project',
          bucket='reviver-bucket',
          builder='fake-runner',
          builder_group=None,
      ),
      api.chromium_polymorphic.triggered_properties(
          project='fake-project',
          bucket='fake-bucket',
          builder='fake-builder',
          builder_group='fake-group',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder='fake-builder',
              builder_group='fake-group',
          ).assemble()),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'fake-gtest',
                  }],
                  'scripts': [{
                      'name': 'fake-script-test',
                      'script': 'fake-script',
                  }],
              },
          }),
      api.step_data('compile', retcode=1),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'test-failure',
      api.chromium.generic_build(
          project='reviver-project',
          bucket='reviver-bucket',
          builder='fake-runner',
          builder_group=None,
      ),
      api.chromium_polymorphic.triggered_properties(
          project='fake-project',
          bucket='fake-bucket',
          builder='fake-builder',
          builder_group='fake-group',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder='fake-builder',
              builder_group='fake-group',
          ).assemble()),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'fake-gtest',
                  }],
                  'scripts': [{
                      'name': 'fake-script-test',
                      'script': 'fake-script',
                  }],
              },
          }),
      api.override_step_data(
          'fake-gtest results',
          stdout=api.raw_io.output_text(
              api.test_utils.rdb_results(
                  'fake-gtest', failing_tests=['foo', 'bar']))),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'test-infra-failure',
      api.chromium.generic_build(
          project='reviver-project',
          bucket='reviver-bucket',
          builder='fake-runner',
          builder_group=None,
      ),
      api.chromium_polymorphic.triggered_properties(
          project='fake-project',
          bucket='fake-bucket',
          builder='fake-builder',
          builder_group='fake-group',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder='fake-builder',
              builder_group='fake-group',
          ).assemble()),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'fake-gtest',
                  }],
                  'scripts': [{
                      'name': 'fake-script-test',
                      'script': 'fake-script',
                  }],
              },
          }),
      api.override_step_data('fake-gtest', retcode=1),
      api.expect_status('INFRA_FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'process-coverage',
      api.code_coverage(use_clang_coverage=True),
      api.chromium.generic_build(
          project='reviver-project',
          bucket='reviver-bucket',
          builder='fake-runner',
          builder_group=None,
      ),
      api.chromium_polymorphic.triggered_properties(
          project='fake-project',
          bucket='fake-bucket',
          builder='fake-builder',
          builder_group='fake-group',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder='fake-builder',
              builder_group='fake-group',
          ).assemble()),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'fake-gtest',
                  }],
                  'scripts': [{
                      'name': 'fake-script-test',
                      'script': 'fake-script',
                  }],
              },
          }),
      api.post_process(post_process.MustRunRE, '.*coverage data.*'),
      api.post_process(post_process.DropExpectation),
  )
