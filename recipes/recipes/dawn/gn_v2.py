# Copyright 2025 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Recipe used by standalone Dawn CI builders which use GN (instead of cmake)"""

from recipe_engine import post_process

from RECIPE_MODULES.build.chromium_tests_builder_config import (builder_db,
                                                                builder_spec)

DEPS = [
    'chromium_tests',
    'dawn',
    'recipe_engine/platform',
]


def RunSteps(api):
  return api.dawn.ci_steps()


_TEST_BUILDERS = builder_db.BuilderDatabase.create({
    'dawn': {
        'linux':
            builder_spec.BuilderSpec.create(
                gclient_config='dawn', chromium_config='dawn_base'),
    },
})

_TEST_SPECS = {
    'linux': {
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
}


def GenTests(api):
  yield api.test(
      'linux',
      api.platform('linux', 64),
      api.dawn.ci_build(builder='linux'),
      api.dawn.builders(_TEST_BUILDERS),
      api.chromium_tests.read_targets_spec('dawn', _TEST_SPECS),
      api.post_process(post_process.StepSuccess, 'Test statistics'),
      api.post_process(post_process.DropExpectation),
  )
