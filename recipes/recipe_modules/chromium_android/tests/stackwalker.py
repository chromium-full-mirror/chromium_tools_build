# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import DropExpectation, StepSuccess

DEPS = [
  'chromium',
  'chromium_android',
  'recipe_engine/path',
]


def RunSteps(api):
  api.chromium.set_config('chromium')
  api.chromium_android.stackwalker(
      api.path['checkout'],
      [api.chromium.output_dir.join('lib.unstripped', 'libchrome.so')])


def GenTests(api):
  yield api.test(
      'basic',
      api.path.exists(
          api.path['checkout'].join('out', 'Release', 'lib.unstripped',
                                    'libchrome.so'),
          api.path['checkout'].join('out', 'Release', 'microdump_stackwalk'),
          api.path['checkout'].join('out', 'Release', 'dump_syms'),
      ),
      api.post_process(StepSuccess,
                       'generate breakpad symbols for libchrome.so'),
      api.post_process(StepSuccess, 'symbolized breakpad crashes'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'missing_binaries',
      api.post_process(StepSuccess, 'skipping stackwalker step'),
      api.post_process(DropExpectation),
  )
