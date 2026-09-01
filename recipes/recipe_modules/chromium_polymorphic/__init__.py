# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.recipe_modules.build.chromium_polymorphic import properties

DEPS = [
    'chromium_tests_builder_config',
    'recipe_engine/buildbucket',
    'recipe_engine/properties',
    'recipe_engine/step',
]

PROPERTIES = properties.InputProperties

from .api import ChromiumPolymorphicApi as API
from .test_api import ChromiumPolymorphicTestApi as TEST_API
