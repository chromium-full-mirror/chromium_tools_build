# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Tests for angle.try_steps() with synthetic builders."""

from __future__ import annotations

from recipe_engine import post_process

from RECIPE_MODULES.build.chromium_tests_builder_config import (builder_db,
                                                                builder_spec,
                                                                try_spec)

DEPS = [
    'chromium',
    'chromium_tests',
    'angle_v2',
    'recipe_engine/platform',
    'recipe_engine/step',
]


def RunSteps(api):
  api.angle_v2.try_steps()
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


def GenTests(api):
  yield api.test(
      'not_a_tryjob',
      api.platform('linux', 64),
      api.angle_v2.ci_build(builder='try-linux-compile-and-test',),
      api.angle_v2.builders(_TEST_BUILDERS),
      api.angle_v2.trybots(_TEST_TRYBOTS),
      api.post_check(post_process.MustRun, 'not a tryjob'),
      api.expect_status('INFRA_FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'try_linux_compile_and_test',
      api.platform('linux', 64),
      api.angle_v2.try_build(builder='try-linux-compile-and-test'),
      api.angle_v2.builders(_TEST_BUILDERS),
      api.angle_v2.trybots(_TEST_TRYBOTS),
      api.chromium_tests.read_targets_spec('angle', _TEST_SPECS),
      api.post_process(post_process.StepSuccess, 'compile (with patch)'),
      api.post_process(post_process.StepSuccess,
                       'angle_unittests (with patch) on Ubuntu'),
      api.post_process(post_process.StepSuccess, 'Success'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'try_linux_parent_child',
      api.platform('linux', 64),
      api.angle_v2.try_build(builder='try-linux-parent-child'),
      api.angle_v2.builders(_TEST_BUILDERS),
      api.angle_v2.trybots(_TEST_TRYBOTS),
      api.chromium_tests.read_targets_spec('angle', _TEST_SPECS),
      api.post_process(post_process.StepSuccess, 'compile (with patch)'),
      api.post_process(post_process.StepSuccess,
                       'angle_end2end_tests (with patch) on Ubuntu'),
      api.post_process(post_process.StepSuccess, 'Success'),
      api.post_process(post_process.DropExpectation),
  )
