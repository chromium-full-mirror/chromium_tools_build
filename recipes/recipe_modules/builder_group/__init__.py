# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


DEPS = [
    'recipe_engine/properties',
]

from .api import BuilderGroupApi as API
from .test_api import ChromiumTestApi as TEST_API
