# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


DEPS = [
    'depot_tools/depot_tools',
    'depot_tools/gerrit',
    'depot_tools/git',
    'depot_tools/gsutil',
    'recipe_engine/buildbucket',
    'recipe_engine/change_verifier',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/proto',
    'recipe_engine/raw_io',
    'recipe_engine/resultdb',
    'recipe_engine/step',
    'v8',
]

from .api import V8RollWatcherApi as API
