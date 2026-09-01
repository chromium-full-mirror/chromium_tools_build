# Copyright (c) 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.recipe_modules.build.pgo import properties

DEPS = [
    'chromium',
    'code_coverage',
    'depot_tools/git',
    'depot_tools/gitiles',
    'depot_tools/gsutil',
    'gn',
    'profiles',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/step',
]

PROPERTIES = properties.InputProperties

from .api import PgoApi as API
from .test_api import PgoTestApi as TEST_API
