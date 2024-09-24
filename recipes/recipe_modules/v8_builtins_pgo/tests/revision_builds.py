# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import (
    DoesNotRun,
    DropExpectation,
    MustRun,
)

DEPS = [
    'v8_builtins_pgo',
    'recipe_engine/buildbucket',
]


def RunSteps(api):
  return api.v8_builtins_pgo.run(compilators=['x64'])


def GenTests(api):
  pgo_api = api.v8_builtins_pgo

  yield api.test(
      'ci',
      api.buildbucket.ci_build(bucket='ci', revision='c0ffee15'),
      *pgo_api.mock_compilation(['c0ffee15'], ['x64']),
      *pgo_api.mock_profiles(['c0ffee15'], ['x64']),
      api.post_process(
          MustRun,
          'trigger compilators.c0ffee15 x64',
          'download benchmark code',
          'collect compilation isolates.c0ffee15 x64',
          'merge isolate with benchmark.c0ffee15 x64',
          'trigger profilers.c0ffee15 x64',
          'validate profiles.read profile for c0ffee15 x64',
      ),
      api.post_process(
          DoesNotRun,
          'trigger compilators.c0ffee15 x86',
          'upload to gs',
          'assign pgo tags',
          'add_comment_to_gerrit_changes',
          'report exceptions.gsutil upload blocked-versions.txt',
      ),
      api.post_process(DropExpectation),
  )
