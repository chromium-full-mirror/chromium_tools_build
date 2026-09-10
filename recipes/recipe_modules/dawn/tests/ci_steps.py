# Copyright 2025 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Tests for dawn.ci_steps() with synthetic builders."""

from __future__ import annotations

from RECIPE_MODULES.build.chromium_tests_builder_config import (
  builder_db,
  builder_spec,
)

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium_tests, dawn
from RECIPE_MODULES.recipe_engine import platform, properties, step


@dataclass
class DEPS(RecipeScriptApi):
  chromium_tests: chromium_tests.API
  dawn: dawn.API
  platform: platform.API
  properties: properties.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium_tests: chromium_tests.TEST_API
  dawn: dawn.TEST_API
  platform: platform.TEST_API
  properties: properties.TEST_API


def RunSteps(api: DEPS):
  api.dawn.ci_steps()
  api.step('Success', ['echo', 'Success!'])


_TEST_BUILDERS = builder_db.BuilderDatabase.create(
  {
    'dawn': {
      'linux-compile-and-test': builder_spec.BuilderSpec.create(
        gclient_config='dawn', chromium_config='dawn_base'
      ),
      'linux-parent-builder': builder_spec.BuilderSpec.create(
        gclient_config='dawn', chromium_config='dawn_base'
      ),
      'linux-child-tester': builder_spec.BuilderSpec.create(
        gclient_config='dawn',
        chromium_config='dawn_base',
        parent_builder_group='dawn',
        parent_buildername='linux-parent-builder',
        execution_mode=builder_spec.TEST,
      ),
      'win-compile-and-test': builder_spec.BuilderSpec.create(
        gclient_config='dawn', chromium_config='dawn_base'
      ),
      'win-parent-builder': builder_spec.BuilderSpec.create(
        gclient_config='dawn', chromium_config='dawn_base'
      ),
      'win-child-tester': builder_spec.BuilderSpec.create(
        gclient_config='dawn',
        chromium_config='dawn_base',
        parent_builder_group='dawn',
        parent_buildername='win-parent-builder',
        execution_mode=builder_spec.TEST,
      ),
    },
  }
)

_TEST_SPECS = {
  'linux-compile-and-test': {
    'gtest_tests': [
      {
        'test': 'dawn_unittests',
        'swarming': {
          'dimensions': {
            'os': 'Ubuntu',
            'pool': 'chromium.tests.gpu',
          },
        },
      },
    ],
  },
  'linux-child-tester': {
    'gtest_tests': [
      {
        'test': 'dawn_end2end_tests',
        'swarming': {
          'dimensions': {
            'os': 'Ubuntu',
            'pool': 'chromium.tests.gpu',
          },
        },
      },
    ],
  },
  'win-compile-and-test': {
    'gtest_tests': [
      {
        'test': 'dawn_unittests',
        'swarming': {
          'dimensions': {
            'os': 'Windows',
            'pool': 'chromium.tests.gpu',
          },
        },
      },
    ],
  },
  'win-child-tester': {
    'gtest_tests': [
      {
        'test': 'dawn_end2end_tests',
        'swarming': {
          'dimensions': {
            'os': 'Windows',
            'pool': 'chromium.tests.gpu',
          },
        },
      },
    ],
  },
}


def GenTests(api: TEST_DEPS):
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
      },
    ),
  )

  yield api.test(
    'win_compile_and_test',
    api.platform('win', 64),
    api.dawn.ci_build(builder='win-compile-and-test'),
    api.dawn.builders(_TEST_BUILDERS),
    api.chromium_tests.read_targets_spec('dawn', _TEST_SPECS),
  )

  yield api.test(
    'win_parent_builder',
    api.platform('win', 64),
    api.dawn.ci_build(builder='win-parent-builder'),
    api.dawn.builders(_TEST_BUILDERS),
    api.chromium_tests.read_targets_spec('dawn', _TEST_SPECS),
  )

  yield api.test(
    'win_child_tester',
    api.platform('win', 64),
    api.dawn.ci_build(builder='win-child-tester'),
    api.dawn.builders(_TEST_BUILDERS),
    api.chromium_tests.read_targets_spec('dawn', _TEST_SPECS),
    api.properties(
      swarm_hashes={
        'dawn_end2end_tests': 'ffffffffffffffffffffffffffffff/size',
      },
    ),
  )
