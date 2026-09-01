# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.recipe_modules.build.chromium_swarming import properties as properties_pb

DEPS = [
    'builder_group',
    'chromium',
    'chromium_checkout',
    'code_coverage',
    'depot_tools/tryserver',
    'presentation_utils',
    'recipe_engine/buildbucket',
    'recipe_engine/cas',
    'recipe_engine/cipd',
    'recipe_engine/context',
    'recipe_engine/cv',
    'recipe_engine/led',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/resultdb',
    'recipe_engine/raw_io',
    'recipe_engine/runtime',
    'recipe_engine/step',
    'recipe_engine/swarming',
    'repro_instructions',
]

PROPERTIES = properties_pb.InputProperties

# TODO(phajdan.jr): provide coverage (http://crbug.com/693058).
DISABLE_STRICT_COVERAGE = True

from .api import SwarmingTask
from .types import CipdPackage, MergeScript, TriggerScript

from .api import SwarmingApi as API
from .test_api import SwarmingTestApi as TEST_API
