# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


DEPS = [
    'chromium_checkout',
    'recipe_engine/buildbucket',
    'recipe_engine/cipd',
    'recipe_engine/json',
    'recipe_engine/raw_io',
    'recipe_engine/path',
    'recipe_engine/runtime',
    'recipe_engine/step',
    'recipe_engine/swarming',
    'recipe_engine/time',
    'test_utils',
]

from .api import SkylabApi as API
from .test_api import SkylabTestApi as TEST_API
