# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

DEPS = [
    'chromium',
    'recipe_engine/buildbucket',
    'recipe_engine/runtime',
    'recipe_engine/step',
    'recipe_engine/uuid',
]

from .api import ChromiumTurbociApi as API
