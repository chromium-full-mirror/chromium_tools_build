# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import (DropExpectation, StatusSuccess)

DEPS = [
    'goma',
    'recipe_engine/platform',
    'recipe_engine/properties',
]


def RunSteps(api):
  jobs = api.goma.jobs
  expected_jobs = (
      api.m.properties.get('expected_jobs') or api.m.platform.cpu_count * 10)
  assert jobs == expected_jobs


def GenTests(api):
  yield api.test(
      'default-is-based-off-cpu-count',
      api.platform.name('linux'),
      api.properties(expected_jobs=None),
      api.post_process(StatusSuccess),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'can-be-overridden-99',
      api.goma(jobs=99),
      api.properties(expected_jobs=99),
      api.post_process(StatusSuccess),
      api.post_process(DropExpectation),
  )
