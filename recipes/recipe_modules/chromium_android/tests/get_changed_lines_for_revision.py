# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import (DropExpectation, StepCommandContains,
                                        StepSuccess)

DEPS = [
    'recipe_engine/path',
    'chromium_android',
]


def RunSteps(api):
  api.path.checkout_dir = api.path.cache_dir / 'builder' / 'src'
  api.chromium_android.get_changed_lines_for_revision()


def GenTests(api):
  yield api.test(
      'basic',
      api.post_process(StepSuccess,
                       'Finding changed files matching diff filter: A'),
      api.post_process(StepSuccess,
                       'Finding changed files matching diff filter: M'),
      api.post_process(StepCommandContains,
                       'Saving changed lines for revision.', [
                           '{"fake/file1.java": [],'
                           ' "fake/file2.java": [],'
                           ' "fake/file3.java": []}',
                       ]),
      api.post_process(DropExpectation),
  )
