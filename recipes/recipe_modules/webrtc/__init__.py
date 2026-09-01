# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


DEPS = [
    'archive',
    'builder_group',
    'chromium',
    'chromium_android',
    'chromium_checkout',
    'chromium_swarming',
    'chromium_tests',
    'chromium_tests_builder_config',
    'code_coverage',
    'depot_tools/bot_update',
    'depot_tools/depot_tools',
    'depot_tools/gclient',
    'depot_tools/git',
    'depot_tools/gitiles',
    'depot_tools/gsutil',
    'depot_tools/tryserver',
    'infra/zip',
    'isolate',
    'filter',
    'flakiness',
    'perf_dashboard',
    'profiles',
    'recipe_engine/buildbucket',
    'recipe_engine/cas',
    'recipe_engine/cipd',
    'recipe_engine/commit_position',
    'recipe_engine/context',
    'recipe_engine/cq',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/resultdb',
    'recipe_engine/runtime',
    'recipe_engine/scheduler',
    'recipe_engine/service_account',
    'recipe_engine/step',
    'siso',
    'test_utils',
]


# TODO(phajdan.jr): provide coverage (http://crbug.com/693058).
DISABLE_STRICT_COVERAGE = True

from .api import WebRTCApi as API
from .test_api import WebRTCTestApi as TEST_API
