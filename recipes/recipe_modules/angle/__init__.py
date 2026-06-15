# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.recipe_modules.build.angle import properties

DEPS = [
    'builder_group',
    'chromium',
    'chromium_android',
    'chromium_checkout',
    'chromium_swarming',
    'chromium_tests',
    'chromium_tests_builder_config',
    'depot_tools/bot_update',
    'depot_tools/depot_tools',
    'depot_tools/gclient',
    'depot_tools/tryserver',
    'flakiness',
    'gn',
    'isolate',
    'presentation_utils',
    'recipe_engine/buildbucket',
    'recipe_engine/commit_position',
    'recipe_engine/context',
    'recipe_engine/cq',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/step',
    'recipe_engine/swarming',
    'recipe_engine/time',
    'siso',
    'test_utils',
]

PROPERTIES = properties.InputProperties

from .api import ANGLEApi as API
from .test_api import ANGLETestsApi as TEST_API
