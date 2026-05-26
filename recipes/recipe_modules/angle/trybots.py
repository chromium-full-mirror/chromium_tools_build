# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine.engine_types import freeze

from RECIPE_MODULES.build.chromium_tests_builder_config import try_spec


def _create_compile_spec(buildername):
  return try_spec.TrySpec.create_for_single_mirror(
      builder_group='angle',
      buildername=buildername,
      is_compile_only=True,
  )


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
    'win-msvc-compile':
        _create_compile_spec('win-msvc-compile'),
    'win-msvc-dbg-compile':
        _create_compile_spec('win-msvc-dbg-compile'),
    'win-msvc-x86-compile':
        _create_compile_spec('win-msvc-x86-compile'),
    'win-msvc-x86-dbg-compile':
        _create_compile_spec('win-msvc-x86-dbg-compile'),
    'winuwp-compile':
        _create_compile_spec('winuwp-compile'),
    'winuwp-dbg-compile':
        _create_compile_spec('winuwp-dbg-compile'),
}

TRYBOTS = try_spec.TryDatabase.create({
    'angle': _SPEC,
})
