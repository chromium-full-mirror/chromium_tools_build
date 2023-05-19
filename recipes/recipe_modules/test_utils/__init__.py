# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from PB.recipe_modules.build.test_utils import properties

DEPS = [
    'build',
    'chromium',
    'chromium_swarming',
    'flakiness',
    'depot_tools/tryserver',
    'presentation_utils',
    'recipe_engine/buildbucket',
    'recipe_engine/futures',
    'recipe_engine/json',
    'recipe_engine/legacy_annotation',
    'recipe_engine/luci_analysis',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/resultdb',
    'recipe_engine/step',
    'recipe_engine/time',
    'skylab',
]

PROPERTIES = properties.InputProperties

# TODO(phajdan.jr): provide coverage (http://crbug.com/693058).
DISABLE_STRICT_COVERAGE = True
