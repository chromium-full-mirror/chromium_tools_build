# Copyright 2025 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

DEPS = [
    'chromium',
    'chromium_tests',
    'chromium_tests_builder_config',
    'depot_tools/tryserver',
    'recipe_engine/cipd',
    'recipe_engine/platform',
    'recipe_engine/step',
]

from .api import DawnApi as API
from .test_api import DawnTestsApi as TEST_API
