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

from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
    chromium,
    chromium_polymorphic,
    chromium_reviver,
    chromium_tests,
    chromium_tests_builder_config,
)


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_polymorphic: chromium_polymorphic.API
  chromium_reviver: chromium_reviver.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API
  chromium_polymorphic: chromium_polymorphic.TEST_API
  chromium_tests: chromium_tests.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API


def RunSteps(api: DEPS):
  return api.chromium_reviver.run()


def GenTests(api: TEST_DEPS):
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
      api.chromium_tests.read_targets_spec('fake-group', {
          'fake-builder': {
              'gtest_tests': [{
                  'test': 'fake-gtest',
              }],
          },
      }),
      api.post_process(post_process.DropExpectation),
  )
