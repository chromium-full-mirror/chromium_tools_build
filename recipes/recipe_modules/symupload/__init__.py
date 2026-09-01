# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.recipe_modules.build.symupload import properties

DEPS = [
    'chromium',
    'chromium_checkout',
    'infra/cloudkms',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/step',
    'recipe_engine/time',
]

PROPERTIES = properties.InputProperties

from .api import SymuploadApi as API
from .test_api import SymuploadTestApi as TEST_API
