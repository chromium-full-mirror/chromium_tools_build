# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


DEPS = [
    'chromium',
    'chromium_polymorphic',
    'chromium_swarming',
    'chromium_tests',
    'code_coverage',
    'recipe_engine/buildbucket',
]

from .api import ChromiumReviverApi as API
