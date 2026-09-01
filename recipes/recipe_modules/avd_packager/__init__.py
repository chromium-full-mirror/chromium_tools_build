# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.recipe_modules.build.avd_packager import properties

DEPS = [
    'chromium_checkout',
    'depot_tools/gclient',
    'recipe_engine/buildbucket',
    'recipe_engine/cipd',
    'recipe_engine/context',
    'recipe_engine/defer',
    'recipe_engine/json',
    'recipe_engine/step',
]

PROPERTIES = properties.InputProperties

from .api import AvdPackagerApi as API
