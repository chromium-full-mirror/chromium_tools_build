# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.recipe_modules.build.siso import properties

from .api import RUSAGE_FORMAT

DEPS = [
    'depot_tools/gclient',
    'depot_tools/gsutil',
    'depot_tools/tryserver',
    'recipe_engine/buildbucket',
    'recipe_engine/cas',
    'recipe_engine/cipd',
    'recipe_engine/context',
    'recipe_engine/futures',
    'recipe_engine/json',
    'recipe_engine/led',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/runtime',
    'recipe_engine/step',
    'recipe_engine/time',
    'recipe_engine/uuid',
    'repro_instructions',
]

PROPERTIES = properties.InputProperties

from .api import SisoApi as API
from .test_api import SisoTestApi as TEST_API
