# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


DEPS = [
    'recipe_engine/buildbucket',
    'recipe_engine/json',
    'recipe_engine/led',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/resultdb',
    'recipe_engine/step',
]

from .api import ReproInstructionsApi as API
