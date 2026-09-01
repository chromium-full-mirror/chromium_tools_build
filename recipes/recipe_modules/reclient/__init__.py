# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.recipe_modules.build.reclient import properties
from PB.recipe_modules.build.reclient import rbe_metrics_bq

DEPS = [
    'depot_tools/gclient',
    'depot_tools/gsutil',
    'ninjalog',
    'recipe_engine/archive',
    'recipe_engine/buildbucket',
    'recipe_engine/file',
    'recipe_engine/cipd',
    'recipe_engine/context',
    'recipe_engine/json',
    'recipe_engine/led',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/runtime',
    'recipe_engine/step',
    'recipe_engine/time',
    'recipe_engine/uuid',
    'siso',
]

PROPERTIES = properties.InputProperties

from .api import ReclientApi as API
from .test_api import ReclientTestApi as TEST_API
