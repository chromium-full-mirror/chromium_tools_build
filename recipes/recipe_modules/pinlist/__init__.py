# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.recipe_modules.build.pinlist import properties

DEPS = [
    'chromium',
    'chromium_checkout',
    'recipe_engine/bcid_reporter',
    'recipe_engine/buildbucket',
    'recipe_engine/cipd',
    'recipe_engine/file',
    'recipe_engine/led',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/step',
]

PROPERTIES = properties.InputProperties

from .api import PinlistApi as API
from .test_api import PinlistTestApi as TEST_API
