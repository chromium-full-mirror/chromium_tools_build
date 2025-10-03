# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from PB.recipe_modules.build.chromium_3pp import properties

DEPS = [
    'chromium_checkout',
    'depot_tools/gclient',
    'depot_tools/git',
    'depot_tools/tryserver',
    'infra/support_3pp',
    'recipe_engine/context',
    'recipe_engine/path',
    'recipe_engine/raw_io',
    'recipe_engine/runtime',
    'recipe_engine/step',
]

PROPERTIES = properties.InputProperties
