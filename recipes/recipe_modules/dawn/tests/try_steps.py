# Copyright 2025 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Tests for dawn.ci_steps() with synthetic builders."""

from __future__ import annotations

from recipe_engine.post_process import DropExpectation, MustRun

from RECIPE_MODULES.build.chromium_tests_builder_config import (
  builder_db,
  builder_spec,
  try_spec,
)

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium, chromium_tests, dawn
from RECIPE_MODULES.recipe_engine import platform, step


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_tests: chromium_tests.API
  dawn: dawn.API
  platform: platform.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium_tests: chromium_tests.TEST_API
  dawn: dawn.TEST_API
  platform: platform.TEST_API


def RunSteps(api: DEPS):
  api.dawn.try_steps()
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
    },
  }
)

_TEST_TRYBOTS = try_spec.TryDatabase.create(
  {
    'dawn': {
      'try-linux-compile-and-test': try_spec.TrySpec.create(
        mirrors=[
          try_spec.TryMirror.create(
            builder_group='dawn',
            buildername='linux-compile-and-test',
          ),
        ]
      ),
      'try-linux-parent-child': try_spec.TrySpec.create(
        mirrors=[
          try_spec.TryMirror.create(
            builder_group='dawn',
            buildername='linux-parent-builder',
            tester='linux-child-tester',
          ),
        ]
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
}


def GenTests(api: TEST_DEPS):
  yield api.test(
    'not_a_tryjob',
    api.platform('linux', 64),
    api.dawn.ci_build(
      builder='try-linux-compile-and-test',
    ),
    api.dawn.builders(_TEST_BUILDERS),
    api.dawn.trybots(_TEST_TRYBOTS),
    api.post_check(MustRun, 'not a tryjob'),
    api.expect_status('INFRA_FAILURE'),
    api.post_process(DropExpectation),
  )

  yield api.test(
    'try_linux_compile_and_test',
    api.platform('linux', 64),
    api.dawn.try_build(builder='try-linux-compile-and-test'),
    api.dawn.builders(_TEST_BUILDERS),
    api.dawn.trybots(_TEST_TRYBOTS),
    api.chromium_tests.read_targets_spec('dawn', _TEST_SPECS),
  )

  yield api.test(
    'try_linux_parent_child',
    api.platform('linux', 64),
    api.dawn.try_build(builder='try-linux-parent-child'),
    api.dawn.builders(_TEST_BUILDERS),
    api.dawn.trybots(_TEST_TRYBOTS),
    api.chromium_tests.read_targets_spec('dawn', _TEST_SPECS),
  )
