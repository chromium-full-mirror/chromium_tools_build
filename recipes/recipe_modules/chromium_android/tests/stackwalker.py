# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine.post_process import DropExpectation, StepSuccess

DEPS = [
    'chromium',
    'chromium_android',
    'recipe_engine/path',
]


def RunSteps(api):
  source_dir = api.path.start_dir / 'checkout'
  api.chromium.set_config('chromium')
  build_dir = api.chromium.default_build_dir(source_dir)

  api.chromium_android.stackwalker(source_dir,
                                   [build_dir / 'lib.unstripped/libchrome.so'])


def GenTests(api):
  yield api.test(
      'basic',
      api.chromium.ci_build(),
      api.path.exists(
          api.path.start_dir /
          'checkout/out/2796-Linux_Builder/lib.unstripped/libchrome.so',
          api.path.start_dir /
          'checkout/out/2796-Linux_Builder/microdump_stackwalk',
          api.path.start_dir / 'checkout/out/2796-Linux_Builder/dump_syms',
      ),
      api.post_process(StepSuccess,
                       'generate breakpad symbols for libchrome.so'),
      api.post_process(StepSuccess, 'symbolized breakpad crashes'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'missing_binaries',
      api.chromium.ci_build(),
      api.post_process(StepSuccess, 'skipping stackwalker step'),
      api.post_process(DropExpectation),
  )
