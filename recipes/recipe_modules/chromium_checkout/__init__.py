# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.recipe_modules.build.chromium_checkout import properties

DEPS = [
    'chromium',
    'chromium_bootstrap',
    'depot_tools/bot_update',
    'depot_tools/gclient',
    'depot_tools/tryserver',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/resultdb',
    'recipe_engine/runtime',
    'recipe_engine/step',
    'repro_instructions',
    'siso',
]

PROPERTIES = properties.InputProperties

from .api import ChromiumCheckoutApi as API
from .test_api import ChromiumCheckoutTestApi as TEST_API
