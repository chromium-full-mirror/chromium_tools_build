# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

DEPS = [
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/step',
]

# Don't set properties, just let the recipes set the properties to the
# properties proto defined in this module

# Forward symbols that might need to be imported
from .api import BlockerCategory