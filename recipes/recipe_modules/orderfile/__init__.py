# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.recipe_modules.build.orderfile import properties

DEPS = [
    'chromium',
    'profiles',
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

from .api import OrderfileApi as API
from .test_api import OrderfileTestApi as TEST_API
