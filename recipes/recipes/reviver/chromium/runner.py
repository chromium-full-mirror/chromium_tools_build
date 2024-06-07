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

DEPS = [
    'chromium',
    'chromium_polymorphic',
    'chromium_reviver',
    'chromium_tests',
    'chromium_tests_builder_config',
]


def RunSteps(api):
  return api.chromium_reviver.run()


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
      api.chromium_tests.read_targets_spec('fake-group', {
          'fake-builder': {
              'gtest_tests': [{
                  'test': 'fake-gtest',
              }],
          },
      }),
      api.post_process(post_process.DropExpectation),
  )
