# Copyright (c) 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.recipe_modules.build.flakiness import properties

DEPS = [
    'chromium',
    'chromium_checkout',
    'depot_tools/bot_update',
    'depot_tools/gerrit',
    'depot_tools/gsutil',
    'depot_tools/tryserver',
    'isolate',
    'recipe_engine/buildbucket',
    'recipe_engine/commit_position',
    'recipe_engine/file',
    'recipe_engine/futures',
    'recipe_engine/json',
    'recipe_engine/led',
    'recipe_engine/luci_analysis',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/resultdb',
    'recipe_engine/runtime',
    'recipe_engine/step',
    'recipe_engine/time',
    'tar',
]

PROPERTIES = properties.InputProperties

from .api import FlakinessApi as API
from .test_api import FlakinessTestApi as TEST_API
