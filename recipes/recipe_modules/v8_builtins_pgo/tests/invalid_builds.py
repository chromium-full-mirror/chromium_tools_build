# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine.post_process import DropExpectation

DEPS = [
    'v8_builtins_pgo',
    'recipe_engine/buildbucket',
]


def RunSteps(api):
  return api.v8_builtins_pgo.run(compilators=['x64'])


def GenTests(api):
  yield api.test(
      'unsupported-bucket',
      api.buildbucket.ci_build(bucket='unsupported-bucket', revision=None),
      api.expect_exception('AssertionError'),
      api.post_process(DropExpectation),
  )
