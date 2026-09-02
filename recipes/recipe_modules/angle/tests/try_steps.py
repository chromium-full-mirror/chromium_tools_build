# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Tests for angle.try_steps() with synthetic builders."""

from __future__ import annotations

from recipe_engine import post_process

from RECIPE_MODULES.build.chromium_tests_builder_config import (builder_db,
                                                                builder_spec,
                                                                try_spec)

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import angle, chromium, chromium_tests
from RECIPE_MODULES.depot_tools import gclient, tryserver
from RECIPE_MODULES.recipe_engine import platform, properties, step


@dataclass
class DEPS(RecipeScriptApi):
  angle: angle.API
  chromium: chromium.API
  chromium_tests: chromium_tests.API
  gclient: gclient.API
  platform: platform.API
  properties: properties.API
  step: step.API
  tryserver: tryserver.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  angle: angle.TEST_API
  chromium_tests: chromium_tests.TEST_API
  platform: platform.TEST_API
  properties: properties.TEST_API


def RunSteps(api: DEPS):
  res = api.angle.try_steps()
  if api.tryserver.is_tryserver:
    expected_val = False if api.properties.get('no_extra_traces') else None
    custom_vars = api.gclient.c.solutions[0].custom_vars
    assert custom_vars.get('checkout_extra_traces') == expected_val
  if res:
    return res
  api.step('Success', ['echo', 'Success!'])


_TEST_BUILDERS = builder_db.BuilderDatabase.create({
    'angle': {
        'linux-compile-and-test':
            builder_spec.BuilderSpec.create(
                gclient_config='angle', chromium_config='angle_base'),
        'linux-parent-builder':
            builder_spec.BuilderSpec.create(
                gclient_config='angle', chromium_config='angle_base'),
        'linux-child-tester':
            builder_spec.BuilderSpec.create(
                gclient_config='angle',
                chromium_config='angle_base',
                parent_builder_group='angle',
                parent_buildername='linux-parent-builder',
                execution_mode=builder_spec.TEST),
    },
})

_TEST_TRYBOTS = try_spec.TryDatabase.create({
    'angle': {
        'try-linux-compile-and-test':
            try_spec.TrySpec.create(mirrors=[
                try_spec.TryMirror.create(
                    builder_group='angle',
                    buildername='linux-compile-and-test',
                ),
            ]),
        'try-linux-parent-child':
            try_spec.TrySpec.create(mirrors=[
                try_spec.TryMirror.create(
                    builder_group='angle',
                    buildername='linux-parent-builder',
                    tester='linux-child-tester',
                ),
            ]),
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
}


def GenTests(api: TEST_DEPS):
  yield api.test(
      'not_a_tryjob',
      api.platform('linux', 64),
      api.angle.ci_build(builder='try-linux-compile-and-test',),
      api.angle.builders(_TEST_BUILDERS),
      api.angle.trybots(_TEST_TRYBOTS),
      api.post_check(post_process.MustRun, 'not a tryjob'),
      api.expect_status('INFRA_FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'try_linux_compile_and_test',
      api.platform('linux', 64),
      api.properties(no_extra_traces=True),
      api.angle.try_build(builder='try-linux-compile-and-test'),
      api.angle.builders(_TEST_BUILDERS),
      api.angle.trybots(_TEST_TRYBOTS),
      api.chromium_tests.read_targets_spec('angle', _TEST_SPECS),
      api.post_process(post_process.StepSuccess, 'compile (with patch)'),
      api.post_process(post_process.StepSuccess,
                       'angle_unittests (with patch) on Ubuntu'),
      api.post_process(post_process.StepSuccess, 'Success'),
      api.post_process(post_process.DoesNotRunRE, r'.*trace tests.*'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'try_linux_compile_and_test_with_trace_tests_fails',
      api.platform('linux', 64),
      api.properties(run_trace_tests=True),
      api.angle.try_build(builder='try-linux-compile-and-test'),
      api.angle.builders(_TEST_BUILDERS),
      api.angle.trybots(_TEST_TRYBOTS),
      api.chromium_tests.read_targets_spec('angle', _TEST_SPECS),
      api.expect_status('FAILURE'),
      api.post_process(post_process.SummaryMarkdown,
                       'Regular tests and trace tests are mutually exclusive.'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'try_linux_compile_and_test_trace_tests_not_run_on_build_failure',
      api.platform('linux', 64),
      api.properties(run_trace_tests=True),
      api.angle.try_build(builder='try-linux-compile-and-test'),
      api.angle.builders(_TEST_BUILDERS),
      api.angle.trybots(_TEST_TRYBOTS),
      api.chromium_tests.read_targets_spec('angle', _TEST_SPECS),
      api.step_data('compile (with patch)', retcode=1),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DoesNotRunRE, r'.*trace tests.*'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'try_linux_parent_child',
      api.platform('linux', 64),
      api.angle.try_build(builder='try-linux-parent-child'),
      api.angle.builders(_TEST_BUILDERS),
      api.angle.trybots(_TEST_TRYBOTS),
      api.chromium_tests.read_targets_spec('angle', _TEST_SPECS),
      api.post_process(post_process.StepSuccess, 'compile (with patch)'),
      api.post_process(post_process.StepSuccess,
                       'angle_end2end_tests (with patch) on Ubuntu'),
      api.post_process(post_process.StepSuccess, 'Success'),
      api.post_process(post_process.DoesNotRunRE, r'.*trace tests.*'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'try_linux_parent_child_with_trace_tests_fails',
      api.platform('linux', 64),
      api.properties(run_trace_tests=True),
      api.angle.try_build(builder='try-linux-parent-child'),
      api.angle.builders(_TEST_BUILDERS),
      api.angle.trybots(_TEST_TRYBOTS),
      api.chromium_tests.read_targets_spec('angle', _TEST_SPECS),
      api.expect_status('FAILURE'),
      api.post_process(post_process.SummaryMarkdown,
                       'Regular tests and trace tests are mutually exclusive.'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'try_linux_compile_and_test_only_trace_tests',
      api.platform('linux', 64),
      api.properties(run_trace_tests=True),
      api.angle.try_build(builder='try-linux-compile-and-test'),
      api.angle.builders(_TEST_BUILDERS),
      api.angle.trybots(_TEST_TRYBOTS),
      api.chromium_tests.read_targets_spec('angle', {}),
      api.post_process(post_process.StepSuccess, 'Success'),
      api.post_process(post_process.StepSuccess, 'GLES 1.0 trace tests'),
      api.post_process(post_process.StepCommandContains, 'GLES 1.0 trace tests',
                       '--out-dir=[CACHE]/builder/angle/out_CaptureReplayTest'),
      api.post_process(post_process.DropExpectation),
  )
