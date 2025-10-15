# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


DEPS = [
    'depot_tools/tryserver', 'recipe_engine/buildbucket', 'recipe_engine/cv',
    'recipe_engine/file', 'recipe_engine/led', 'recipe_engine/path',
    'recipe_engine/properties', 'recipe_engine/step', 'recipe_engine/swarming',
    'infra/secret_manager'
]

from .api import ChromiumRtsApi as API
