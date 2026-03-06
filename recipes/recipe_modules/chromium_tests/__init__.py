# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.recipe_modules.build.chromium_tests import properties

DEPS = [
    'archive',
    'builder_group',
    'chromium',
    'chromium_android',
    'chromium_bootstrap',
    'chromium_checkout',
    'chromium_rts',
    'chromium_swarming',
    'code_coverage',
    'depot_tools/bot_update',
    'depot_tools/depot_tools',
    'depot_tools/gerrit',
    'depot_tools/gclient',
    'depot_tools/gsutil',
    'depot_tools/tryserver',
    'filter',
    'flakiness',
    'gn',
    'infra/zip',
    'isolate',
    'orderfile',
    'perf_dashboard',
    'pgo',
    'presentation_utils',
    'profiles',
    'recipe_engine/bcid_reporter',
    'recipe_engine/buildbucket',
    'recipe_engine/cas',
    'recipe_engine/commit_position',
    'recipe_engine/context',
    'recipe_engine/cv',
    'recipe_engine/file',
    'recipe_engine/futures',
    'recipe_engine/led',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/resultdb',
    'recipe_engine/runtime',
    'recipe_engine/scheduler',
    'recipe_engine/step',
    'recipe_engine/swarming',
    'recipe_engine/time',
    'recipe_engine/uuid',
    'repro_instructions',
    'siso',
    'skylab',
    'ssci',
    'symupload',
    'test_utils',
]

# Avoid adding a dependency on the chromium_tests_builder_config module, the
# builder config should be self-contained and should be provided to
# chromium_tests; it shouldn't need to lookup any additional builders or access
# the static DBs
assert not any(
    m in DEPS for m in ('chromium_tests_builder_config',
                        'build/chromium_tests_builder_config'))

PROPERTIES = properties.InputProperties

# These can introduce a circular dependency, so import them last.
from .api import ChromiumTestsApi as API
from .test_api import ChromiumTestsApi as TEST_API
