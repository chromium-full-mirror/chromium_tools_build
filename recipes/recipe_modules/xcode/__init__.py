# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from PB.recipe_modules.build.xcode import properties

DEPS = [
    'recipe_engine/file',
    'recipe_engine/path',
    'recipe_engine/json',
    'recipe_engine/properties',
]

PROPERTIES = properties.InputProperties