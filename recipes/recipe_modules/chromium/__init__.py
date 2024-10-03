# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from . import types

from PB.recipe_modules.build.chromium import properties

DEPS = [
    'depot_tools/bot_update',
    'depot_tools/depot_tools',
    # in order to have set_config automatically populate gclient
    'depot_tools/gclient',
    'depot_tools/git',
    'depot_tools/gsutil',
    'depot_tools/tryserver',
    'build',
    'builder_group',
    'gn',
    'ninjalog',
    'reclient',
    'recipe_engine/buildbucket',
    'recipe_engine/cipd',
    'recipe_engine/commit_position',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/led',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/resultdb',
    'recipe_engine/runtime',
    'recipe_engine/step',
    'recipe_engine/uuid',
    'repro_instructions',
    'siso',
    'xcode',
]

PROPERTIES = properties.InputProperties

# Forward symbols for other modules to import
BuilderId = types.BuilderId
BuilderSpec = types.BuilderSpec
from .config import config_ctx as CONFIG_CTX

__all__ = ['CONFIG_CTX', 'BuilderId', 'BuilderSpec']
