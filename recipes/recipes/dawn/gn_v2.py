# Copyright 2025 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Recipe used by standalone Dawn CI builders which use GN (instead of cmake)"""

from recipe_engine import post_process

from RECIPE_MODULES.build.chromium_tests_builder_config import (
  builder_db,
  builder_spec,
)

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium_tests, dawn
from RECIPE_MODULES.recipe_engine import platform


@dataclass
class DEPS(RecipeScriptApi):
  chromium_tests: chromium_tests.API
  dawn: dawn.API
  platform: platform.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium_tests: chromium_tests.TEST_API
  dawn: dawn.TEST_API
  platform: platform.TEST_API


def RunSteps(api: DEPS):
  build_result, _ = api.dawn.ci_steps()
  return build_result


_TEST_BUILDERS = builder_db.BuilderDatabase.create(
  {
    'dawn': {
      'linux': builder_spec.BuilderSpec.create(
        gclient_config='dawn', chromium_config='dawn_base'
      ),
    },
  }
)

_TEST_SPECS = {
  'linux': {
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
}


def GenTests(api: TEST_DEPS):
  yield api.test(
    'linux',
    api.platform('linux', 64),
    api.dawn.ci_build(builder='linux'),
    api.dawn.builders(_TEST_BUILDERS),
    api.chromium_tests.read_targets_spec('dawn', _TEST_SPECS),
    api.post_process(post_process.StepSuccess, 'Test statistics'),
    api.post_process(post_process.DropExpectation),
  )
