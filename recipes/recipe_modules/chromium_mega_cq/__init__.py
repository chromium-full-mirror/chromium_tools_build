# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


DEPS = [
    'chromium',
    'chromium_orchestrator',
    'depot_tools/gerrit',
    'depot_tools/gitiles',
    'depot_tools/tryserver',
    'recipe_engine/buildbucket',
    'recipe_engine/cv',
    'recipe_engine/led',
    'recipe_engine/futures',
    'recipe_engine/json',
    'recipe_engine/raw_io',
    'recipe_engine/step',
    'recipe_engine/swarming',
    'recipe_engine/time',
]

from .api import ChromiumMegaCqApi as API
