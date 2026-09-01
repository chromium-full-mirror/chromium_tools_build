# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.recipe_modules.build.code_coverage import properties


DEPS = [
    'chromium',
    'chromium_checkout',
    'gn',
    'depot_tools/gclient',
    'depot_tools/git',
    'depot_tools/gsutil',
    'depot_tools/tryserver',
    'infra/zip',
    'profiles',
    'siso',
    'recipe_engine/archive',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/led',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/service_account',
    'recipe_engine/step',
    'recipe_engine/swarming',
]


PROPERTIES = properties.InputProperties

from .api import CodeCoverageApi as API
from .test_api import CodeCoverageTestApi as TEST_API
