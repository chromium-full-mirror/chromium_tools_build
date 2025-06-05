# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.


from recipe_engine.recipe_api import Property
from recipe_engine.config import ConfigGroup, List, Single

DEPS = [
    'archive',
    'build',
    'builder_group',
    'chromium',
    'depot_tools/bot_update',
    'depot_tools/gclient',
    'depot_tools/git',
    'depot_tools/gitiles',
    'depot_tools/gsutil',
    'depot_tools/osx_sdk',
    'depot_tools/tryserver',
    'gn',
    'infra/docker',
    'isolate',
    'perf_dashboard',
    'recipe_engine/buildbucket',
    'recipe_engine/commit_position',
    'recipe_engine/context',
    'recipe_engine/cv',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/led',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/runtime',
    'recipe_engine/scheduler',
    'recipe_engine/step',
    'recipe_engine/time',
    'recipe_engine/url',
    'reclient',
    'siso',
    'test_utils',
    'v8_tests',
]

PROPERTIES = {
    '$build/v8':
        Property(
            help='Properties for the v8 module',
            param_name='properties',
            kind=ConfigGroup(
                # Targets to try to isolate even after compilation errors.
                always_isolate_targets=List(str),
                # Whether to use reclient for compilation with the V8 module.
                use_remoteexec=Single(bool),
            ),
            default={},
        ),
}

# TODO(phajdan.jr): provide coverage (http://crbug.com/693058).
DISABLE_STRICT_COVERAGE = True
