# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

DEPS = [
    'chromium',
    'chromium_tests',
    'depot_tools/bot_update',
    'depot_tools/gclient',
    'depot_tools/tryserver',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/step',
    'recipe_engine/properties',
]

from .api import ChromiumTestsTargetsConfigVerifierApi as API
