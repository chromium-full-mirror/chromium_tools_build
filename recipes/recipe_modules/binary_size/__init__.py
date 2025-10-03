# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from PB.recipe_modules.build.binary_size import properties

DEPS = [
    'chromium',
    'chromium_android',
    'chromium_checkout',
    'chromium_tests',
    'depot_tools/bot_update',
    'depot_tools/gclient',
    'depot_tools/gerrit',
    'depot_tools/gitiles',
    'depot_tools/gsutil',
    'depot_tools/tryserver',
    'filter',
    'infra/zip',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/commit_position',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/step',
    'recipe_engine/time',
]

PROPERTIES = properties.InputProperties
