# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

DEPS = [
    'devtools',
    'recipe_engine/buildbucket',
    'depot_tools/tryserver',
]


def RunSteps(api):
  api.devtools.shallow_checkout(depth=2)


def GenTests(api):
  git_repo = 'https://chromium.googlesource.com/devtools/devtools-frontend'

  yield api.test(
      'try_build',
      api.buildbucket.try_build(
          project='devtools',
          builder='try_builder',
          git_repo=git_repo,
          change_number=91827,
          patch_set=1),
      api.post_process(post_process.MustRun, 'git fetch'),
      api.post_process(post_process.MustRun, 'git checkout'),
      api.post_process(post_process.MustRun, 'git reset'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'ci_build',
      api.buildbucket.ci_build(
          project='devtools', builder='ci_builder', git_repo=git_repo),
      api.post_process(post_process.MustRun, 'git fetch'),
      api.post_process(post_process.MustRun, 'git checkout'),
      api.post_process(post_process.DoesNotRun, 'git reset'),
      api.post_process(post_process.DropExpectation),
  )
