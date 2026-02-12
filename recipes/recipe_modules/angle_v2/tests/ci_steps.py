# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Tests for angle.ci_steps() with synthetic builders."""

from __future__ import annotations

from recipe_engine import post_process

from RECIPE_MODULES.build.chromium_tests_builder_config import (builder_db,
                                                                builder_spec)

DEPS = [
    'chromium_tests',
    'angle_v2',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/step',
]


def RunSteps(api):
  res, _ = api.angle_v2.ci_steps()
  if res:
    return res
  api.step('Success', ['echo', 'Success!'])


_TEST_BUILDERS = builder_db.BuilderDatabase.create({
    'angle': {
        'linux-compile-and-test':
            builder_spec.BuilderSpec.create(
                gclient_config='angle_v2', chromium_config='angle_v2_base'),
        'linux-parent-builder':
            builder_spec.BuilderSpec.create(
                gclient_config='angle_v2', chromium_config='angle_v2_base'),
        'linux-child-tester':
            builder_spec.BuilderSpec.create(
                gclient_config='angle_v2',
                chromium_config='angle_v2_base',
                parent_builder_group='angle',
                parent_buildername='linux-parent-builder',
                execution_mode=builder_spec.TEST),
        'linux-clang':
            builder_spec.BuilderSpec.create(
                gclient_config='angle_v2', chromium_config='angle_v2_clang'),
        'win-compile-and-test':
            builder_spec.BuilderSpec.create(
                gclient_config='angle_v2', chromium_config='angle_v2_base'),
        'win-parent-builder':
            builder_spec.BuilderSpec.create(
                gclient_config='angle_v2', chromium_config='angle_v2_base'),
        'win-child-tester':
            builder_spec.BuilderSpec.create(
                gclient_config='angle_v2',
                chromium_config='angle_v2_base',
                parent_builder_group='angle',
                parent_buildername='win-parent-builder',
                execution_mode=builder_spec.TEST),
        'win-clang':
            builder_spec.BuilderSpec.create(
                gclient_config='angle_v2', chromium_config='angle_v2_clang'),
    },
})

_TEST_SPECS = {
    'linux-compile-and-test': {
        'gtest_tests': [{
            'test': 'angle_unittests',
            'swarming': {
                'dimensions': {
                    'os': 'Ubuntu',
                    'pool': 'chromium.tests.gpu',
                },
            },
        },],
    },
    'linux-child-tester': {
        'gtest_tests': [{
            'test': 'angle_end2end_tests',
            'swarming': {
                'dimensions': {
                    'os': 'Ubuntu',
                    'pool': 'chromium.tests.gpu',
                },
            },
        },],
    },
    'linux-clang': {
        'additional_compile_targets': ['angle_unittests',],
    },
    'win-compile-and-test': {
        'gtest_tests': [{
            'test': 'angle_unittests',
            'swarming': {
                'dimensions': {
                    'os': 'Windows',
                    'pool': 'chromium.tests.gpu',
                },
            },
        },],
    },
    'win-child-tester': {
        'gtest_tests': [{
            'test': 'angle_end2end_tests',
            'swarming': {
                'dimensions': {
                    'os': 'Windows',
                    'pool': 'chromium.tests.gpu',
                },
            },
        },],
    },
    'win-clang': {
        'additional_compile_targets': ['angle_unittests',],
    },
}


def GenTests(api):
  yield api.test(
      'linux_compile_and_test',
      api.platform('linux', 64),
      api.angle_v2.ci_build(builder='linux-compile-and-test'),
      api.angle_v2.builders(_TEST_BUILDERS),
      api.chromium_tests.read_targets_spec('angle', _TEST_SPECS),
      api.post_process(post_process.StepSuccess, 'compile'),
      api.post_process(post_process.StepSuccess, 'angle_unittests on Ubuntu'),
      api.post_process(post_process.StepSuccess, 'Success'),
      api.post_process(post_process.DoesNotRunRE, r'.*trace tests.*'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'linux_compile_and_test_with_trace_tests_fails',
      api.platform('linux', 64),
      api.properties(run_trace_tests=True),
      api.angle_v2.ci_build(builder='linux-compile-and-test'),
      api.angle_v2.builders(_TEST_BUILDERS),
      api.chromium_tests.read_targets_spec('angle', _TEST_SPECS),
      api.expect_status('FAILURE'),
      api.post_process(post_process.SummaryMarkdown,
                       'Regular tests and trace tests are mutually exclusive.'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'linux_compile_and_test_trace_tests_not_run_on_build_failure',
      api.platform('linux', 64),
      api.properties(run_trace_tests=True),
      api.angle_v2.ci_build(builder='linux-compile-and-test'),
      api.angle_v2.builders(_TEST_BUILDERS),
      api.chromium_tests.read_targets_spec('angle', _TEST_SPECS),
      api.step_data('compile', retcode=1),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DoesNotRunRE, r'.*trace tests.*'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'linux_parent_builder',
      api.platform('linux', 64),
      api.angle_v2.ci_build(builder='linux-parent-builder'),
      api.angle_v2.builders(_TEST_BUILDERS),
      api.chromium_tests.read_targets_spec('angle', _TEST_SPECS),
      api.post_process(post_process.StepSuccess, 'compile'),
      api.post_process(post_process.DoesNotRunRE, r'test_pre_run.*'),
      api.post_process(post_process.StepSuccess, "trigger"),
      api.post_process(post_process.StepSuccess, 'Success'),
      api.post_process(post_process.DoesNotRunRE, r'.*trace tests.*'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'linux_parent_builder_with_trace_tests_fails',
      api.platform('linux', 64),
      api.properties(run_trace_tests=True),
      api.angle_v2.ci_build(builder='linux-parent-builder'),
      api.angle_v2.builders(_TEST_BUILDERS),
      api.chromium_tests.read_targets_spec('angle', _TEST_SPECS),
      api.expect_status('FAILURE'),
      api.post_process(post_process.SummaryMarkdown,
                       'Regular tests and trace tests are mutually exclusive.'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'linux_child_tester',
      api.platform('linux', 64),
      api.angle_v2.ci_build(builder='linux-child-tester'),
      api.angle_v2.builders(_TEST_BUILDERS),
      api.chromium_tests.read_targets_spec('angle', _TEST_SPECS),
      api.properties(
          swarm_hashes={
              'angle_end2end_tests': 'ffffffffffffffffffffffffffffff/size',
          },),
      api.post_process(post_process.DoesNotRun, 'compile'),
      api.post_process(post_process.StepSuccess,
                       'angle_end2end_tests on Ubuntu'),
      api.post_process(post_process.StepSuccess, 'Success'),
      api.post_process(post_process.DoesNotRunRE, r'.*trace tests.*'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'linux_child_tester_with_trace_tests_fails',
      api.platform('linux', 64),
      api.properties(run_trace_tests=True),
      api.angle_v2.ci_build(builder='linux-child-tester'),
      api.angle_v2.builders(_TEST_BUILDERS),
      api.chromium_tests.read_targets_spec('angle', _TEST_SPECS),
      api.properties(
          swarm_hashes={
              'angle_end2end_tests': 'ffffffffffffffffffffffffffffff/size',
          },),
      api.expect_status('FAILURE'),
      api.post_process(post_process.SummaryMarkdown,
                       'Regular tests and trace tests are mutually exclusive.'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'linux_clang',
      api.platform('linux', 64),
      api.angle_v2.ci_build(builder='linux-clang'),
      api.angle_v2.builders(_TEST_BUILDERS),
      api.chromium_tests.read_targets_spec('angle', _TEST_SPECS),
      api.post_process(post_process.StepSuccess, 'compile'),
      api.post_process(post_process.StepSuccess, 'Success'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'linux_clang_with_trace_tests',
      api.platform('linux', 64),
      api.properties(run_trace_tests=True),
      api.angle_v2.ci_build(builder='linux-clang'),
      api.angle_v2.builders(_TEST_BUILDERS),
      api.chromium_tests.read_targets_spec('angle', _TEST_SPECS),
      api.post_process(post_process.StepSuccess, 'compile'),
      api.post_process(post_process.StepSuccess, 'Success'),
      api.post_process(post_process.StepSuccess, 'GLES 1.0 trace tests'),
      api.post_process(post_process.StepCommandContains, 'GLES 1.0 trace tests',
                       '--out-dir=[CACHE]/builder/angle/out_CaptureReplayTest'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'win_compile_and_test',
      api.platform('win', 64),
      api.angle_v2.ci_build(builder='win-compile-and-test'),
      api.angle_v2.builders(_TEST_BUILDERS),
      api.chromium_tests.read_targets_spec('angle', _TEST_SPECS),
      api.post_process(post_process.StepSuccess, 'compile'),
      api.post_process(post_process.StepSuccess, 'angle_unittests on Windows'),
      api.post_process(post_process.StepSuccess, 'Success'),
      api.post_process(post_process.DoesNotRunRE, r'.*trace tests.*'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'win_compile_and_test_with_trace_tests_fails',
      api.platform('win', 64),
      api.properties(run_trace_tests=True),
      api.angle_v2.ci_build(builder='win-compile-and-test'),
      api.angle_v2.builders(_TEST_BUILDERS),
      api.chromium_tests.read_targets_spec('angle', _TEST_SPECS),
      api.expect_status('FAILURE'),
      api.post_process(post_process.SummaryMarkdown,
                       'Regular tests and trace tests are mutually exclusive.'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'win_parent_builder',
      api.platform('win', 64),
      api.angle_v2.ci_build(builder='win-parent-builder'),
      api.angle_v2.builders(_TEST_BUILDERS),
      api.chromium_tests.read_targets_spec('angle', _TEST_SPECS),
      api.post_process(post_process.StepSuccess, 'compile'),
      api.post_process(post_process.DoesNotRunRE, r'test_pre_run.*'),
      api.post_process(post_process.StepSuccess, "trigger"),
      api.post_process(post_process.StepSuccess, 'Success'),
      api.post_process(post_process.DoesNotRunRE, r'.*trace tests.*'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'win_parent_builder_with_trace_tests_fails',
      api.platform('win', 64),
      api.properties(run_trace_tests=True),
      api.angle_v2.ci_build(builder='win-parent-builder'),
      api.angle_v2.builders(_TEST_BUILDERS),
      api.chromium_tests.read_targets_spec('angle', _TEST_SPECS),
      api.expect_status('FAILURE'),
      api.post_process(post_process.SummaryMarkdown,
                       'Regular tests and trace tests are mutually exclusive.'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'win_child_tester',
      api.platform('win', 64),
      api.angle_v2.ci_build(builder='win-child-tester'),
      api.angle_v2.builders(_TEST_BUILDERS),
      api.chromium_tests.read_targets_spec('angle', _TEST_SPECS),
      api.properties(
          swarm_hashes={
              'angle_end2end_tests': 'ffffffffffffffffffffffffffffff/size',
          },),
      api.post_process(post_process.DoesNotRun, 'compile'),
      api.post_process(post_process.StepSuccess,
                       'angle_end2end_tests on Windows'),
      api.post_process(post_process.StepSuccess, 'Success'),
      api.post_process(post_process.DoesNotRunRE, r'.*trace tests.*'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'win_child_tester_with_trace_tests_fails',
      api.platform('win', 64),
      api.properties(run_trace_tests=True),
      api.angle_v2.ci_build(builder='win-child-tester'),
      api.angle_v2.builders(_TEST_BUILDERS),
      api.chromium_tests.read_targets_spec('angle', _TEST_SPECS),
      api.properties(
          swarm_hashes={
              'angle_end2end_tests': 'ffffffffffffffffffffffffffffff/size',
          },),
      api.expect_status('FAILURE'),
      api.post_process(post_process.SummaryMarkdown,
                       'Regular tests and trace tests are mutually exclusive.'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'win_clang_with_trace_tests',
      api.platform('win', 64),
      api.properties(run_trace_tests=True),
      api.angle_v2.ci_build(builder='win-clang'),
      api.angle_v2.builders(_TEST_BUILDERS),
      api.chromium_tests.read_targets_spec('angle', _TEST_SPECS),
      api.post_process(post_process.StepSuccess, 'compile'),
      api.post_process(post_process.StepSuccess, 'Success'),
      api.post_process(post_process.StepSuccess, 'GLES 1.0 trace tests'),
      api.post_process(
          post_process.StepCommandContains, 'GLES 1.0 trace tests',
          '--out-dir=[CACHE]\\builder\\angle\\out_CaptureReplayTest'),
      api.post_process(post_process.DropExpectation),
  )
