# Copyright 2025 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Tests for dawn.ci_steps() with synthetic builders."""

from __future__ import annotations

from RECIPE_MODULES.build.chromium_tests_builder_config import (builder_db,
                                                                builder_spec)

DEPS = [
    'chromium_tests',
    'dawn',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/step',
]


def RunSteps(api):
  api.dawn.ci_steps()
  api.step('Success', ['echo', 'Success!'])


_TEST_BUILDERS = builder_db.BuilderDatabase.create({
    'dawn': {
        'linux-compile-and-test':
            builder_spec.BuilderSpec.create(
                gclient_config='dawn', chromium_config='dawn_base'),
        'linux-parent-builder':
            builder_spec.BuilderSpec.create(
                gclient_config='dawn', chromium_config='dawn_base'),
        'linux-child-tester':
            builder_spec.BuilderSpec.create(
                gclient_config='dawn',
                chromium_config='dawn_base',
                parent_builder_group='dawn',
                parent_buildername='linux-parent-builder',
                execution_mode=builder_spec.TEST),
    },
})

_TEST_SPECS = {
    'linux-compile-and-test': {
        'gtest_tests': [{
            'test': 'dawn_unittests',
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
            'test': 'dawn_end2end_tests',
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
      'linux_compile_and_test',
      api.platform('linux', 64),
      api.dawn.ci_build(builder='linux-compile-and-test'),
      api.dawn.builders(_TEST_BUILDERS),
      api.chromium_tests.read_targets_spec('dawn', _TEST_SPECS),
  )

  yield api.test(
      'linux_parent_builder',
      api.platform('linux', 64),
      api.dawn.ci_build(builder='linux-parent-builder'),
      api.dawn.builders(_TEST_BUILDERS),
      api.chromium_tests.read_targets_spec('dawn', _TEST_SPECS),
  )

  yield api.test(
      'linux_child_tester',
      api.platform('linux', 64),
      api.dawn.ci_build(builder='linux-child-tester'),
      api.dawn.builders(_TEST_BUILDERS),
      api.chromium_tests.read_targets_spec('dawn', _TEST_SPECS),
      api.properties(
          swarm_hashes={
              'dawn_end2end_tests': 'ffffffffffffffffffffffffffffff/size',
          },),
  )
