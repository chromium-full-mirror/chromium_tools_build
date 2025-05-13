# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from PB.recipe_modules.build.orderfile import properties

DEPS = [
    'recipe_engine/file',
    'recipe_engine/properties',
    'recipe_engine/step',
]

PROPERTIES = properties.InputProperties
