# Copyright 2014 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


DEPS = [
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/step',
]

from .api import AdbApi as API
from .test_api import AdbTestApi as TEST_API
