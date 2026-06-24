# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Recipe used by standalone ANGLE CI builders which re-use chromium_tests."""

from recipe_engine import post_process

from RECIPE_MODULES.build.chromium_tests_builder_config import (builder_db,
                                                                builder_spec)

DEPS = [
    'chromium_tests',
    'angle',
    'recipe_engine/platform',
]


def RunSteps(api):
  build_result, _ = api.angle.ci_steps()
  return build_result


_TEST_BUILDERS = builder_db.BuilderDatabase.create({
    'angle': {
        'linux':
            builder_spec.BuilderSpec.create(
                gclient_config='angle', chromium_config='angle_base'),
    },
})

_TEST_SPECS = {
    'linux': {
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
}


def GenTests(api):
  yield api.test(
      'linux',
      api.platform('linux', 64),
      api.angle.ci_build(builder='linux'),
      api.angle.builders(_TEST_BUILDERS),
      api.chromium_tests.read_targets_spec('angle', _TEST_SPECS),
      api.post_process(post_process.StepSuccess, 'Test statistics'),
      api.post_process(post_process.DropExpectation),
  )
