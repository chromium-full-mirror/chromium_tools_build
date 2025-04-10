# Copyright 2025 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from RECIPE_MODULES.build.chromium_tests_builder_config import (builder_db,
                                                                builder_spec)


def _dawn_spec(**kwargs):
  kwargs.setdefault('chromium_config', 'dawn_base')
  return builder_spec.BuilderSpec.create(**kwargs)


_SPEC = {
    'dawn-linux-x64-builder-rel':
        _dawn_spec(
            gclient_config='dawn',
            simulation_platform='linux',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            },
            execution_mode=builder_spec.COMPILE_AND_TEST,
        ),
    'dawn-linux-x64-sws-rel':
        _dawn_spec(
            parent_builder_group='dawn',
            parent_buildername='dawn-linux-x64-builder-rel',
            gclient_config='dawn',
            simulation_platform='linux',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            },
            execution_mode=builder_spec.TEST,
        ),
}

BUILDERS = builder_db.BuilderDatabase.create({
    'dawn': _SPEC,
})
