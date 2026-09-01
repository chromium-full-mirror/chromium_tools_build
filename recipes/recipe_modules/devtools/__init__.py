# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


DEPS = [
    'chromium',
    'chromium_swarming',
    'depot_tools/bot_update',
    'depot_tools/gclient',
    'depot_tools/git',
    'depot_tools/gsutil',
    'depot_tools/tryserver',
    'recipe_engine/buildbucket',
    'recipe_engine/cas',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/raw_io',
    'recipe_engine/resultdb',
    'recipe_engine/step',
    'recipe_engine/swarming',
    'recipe_engine/url',
    'recipe_engine/futures',
    'v8',
    'v8_tests',
]

from .api import DevToolsAPI as API
