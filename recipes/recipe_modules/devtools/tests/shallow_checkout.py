# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import devtools
from RECIPE_MODULES.depot_tools import tryserver
from RECIPE_MODULES.recipe_engine import buildbucket


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  devtools: devtools.API
  tryserver: tryserver.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API


def RunSteps(api: DEPS):
  api.devtools.shallow_checkout(depth=2)


def GenTests(api: TEST_DEPS):
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
