# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import repo
from RECIPE_MODULES.recipe_engine import assertions, raw_io, step


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  raw_io: raw_io.API
  repo: repo.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  raw_io: raw_io.TEST_API


REPO_LIST_OUTPUT = """\
src/foo : foo
src/bar : bar
badline
"""


def RunSteps(api: DEPS):
  api.repo.init('http://manifest_url')
  api.repo.init('http://manifest_url/manifest', '-b', 'branch')
  api.repo.reset()
  api.repo.clean()
  api.repo.clean('-x')
  api.repo.sync()
  api.repo.manifest()

  repos = api.repo.list()
  api.assertions.assertEqual(repos, [('src/foo', 'foo'), ('src/bar', 'bar')])


def GenTests(api: TEST_DEPS):
  yield api.test(
    'setup_repo',
    api.step_data('repo list', api.raw_io.stream_output_text(REPO_LIST_OUTPUT)),
  )
