# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from RECIPE_MODULES.build.chromium_tests_builder_config import (builder_db,
                                                                builder_spec,
                                                                try_spec)

DEPS = [
    'angle',
    'builder_group',
    'depot_tools/bot_update',
    'depot_tools/depot_tools',
    'depot_tools/gclient',
    'depot_tools/gsutil',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/step',
    'recipe_engine/time',
]


def RunSteps(api):
  return api.angle.steps()


_TEST_BUILDERS = builder_db.BuilderDatabase.create({
    'angle': {
        'linux-builder':
            builder_spec.BuilderSpec.create(
                gclient_config='angle', chromium_config='angle_clang'),
    },
})

_TEST_TRYBOTS = try_spec.TryDatabase.create({
    'angle': {
        'linux-builder':
            try_spec.TrySpec.create(
                mirrors=[
                    try_spec.TryMirror.create(
                        builder_group='angle',
                        buildername='linux-builder',
                    ),
                ],),
    }
})


def GenTests(api):

  yield api.test(
      'linux',
      api.platform('linux', 64),
      api.angle.ci_build(build_number=1234),
      api.angle.builders(_TEST_BUILDERS),
      api.angle.trybots(_TEST_TRYBOTS),
      api.angle.override_commit_pos_data(),
  )
