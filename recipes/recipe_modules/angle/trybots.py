# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine.engine_types import freeze

from RECIPE_MODULES.build.chromium_tests_builder_config import try_spec


_SPEC = {
    'win-asan-test':
        try_spec.TrySpec.create(
            mirrors=[
                try_spec.TryMirror.create(
                    builder_group='angle',
                    buildername='win-asan-test',
                    tester='win10-x64-swiftshader-asan',
                ),
            ],
            analyze_names=['angle'],
            retry_failed_shards=False,
        ),
}

TRYBOTS = try_spec.TryDatabase.create({
    'angle': _SPEC,
})
