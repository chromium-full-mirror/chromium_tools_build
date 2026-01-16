# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Recipe used by standalone ANGLE trybots which re-use chromium_tests."""

from recipe_engine import post_process

from RECIPE_MODULES.build.chromium_tests_builder_config import (builder_db,
                                                                builder_spec,
                                                                try_spec)

DEPS = [
    'chromium_tests',
    'angle_v2',
    'recipe_engine/platform',
]


def RunSteps(api):
  return api.angle_v2.try_steps()


_TEST_BUILDERS = builder_db.BuilderDatabase.create({
    'angle': {
        'linux':
            builder_spec.BuilderSpec.create(
                gclient_config='angle_v2', chromium_config='angle_v2_base'),
    },
})

_TEST_TRYBOTS = try_spec.TryDatabase.create({
    'angle': {
        'try-linux':
            try_spec.TrySpec.create(mirrors=[
                try_spec.TryMirror.create(
                    builder_group='angle',
                    buildername='linux',
                ),
            ]),
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
      api.angle_v2.try_build(builder='try-linux'),
      api.angle_v2.builders(_TEST_BUILDERS),
      api.angle_v2.trybots(_TEST_TRYBOTS),
      api.chromium_tests.read_targets_spec('angle', _TEST_SPECS),
      api.post_process(post_process.StepSuccess, 'Test statistics'),
      api.post_process(post_process.DropExpectation),
  )