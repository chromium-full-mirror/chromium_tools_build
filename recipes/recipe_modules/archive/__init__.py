# Copyright 2013 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.recipe_modules.build.archive import properties

DEPS = [
    'build/ssci',
    'chromium',
    'depot_tools/depot_tools',
    'depot_tools/gitiles',
    'depot_tools/gsutil',
    'infra/zip',
    'recipe_engine/bcid_reporter',
    'recipe_engine/buildbucket',
    'recipe_engine/cipd',
    'recipe_engine/commit_position',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/runtime',
    'recipe_engine/step',
    'recipe_engine/time',
    'squashfs',
    'tar',
]

PROPERTIES = properties.InputProperties

from .api import ArchiveApi as API
from .test_api import ArchiveApi as TEST_API
