# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process
from recipe_engine.recipe_api import Property

from RECIPE_MODULES.depot_tools.gclient import CONFIG_CTX

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium, chromium_android
from RECIPE_MODULES.depot_tools import bot_update, gclient
from RECIPE_MODULES.recipe_engine import context, path, properties


@dataclass
class DEPS(RecipeScriptApi):
  bot_update: bot_update.API
  chromium: chromium.API
  chromium_android: chromium_android.API
  context: context.API
  gclient: gclient.API
  path: path.API
  properties: properties.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API
  path: path.TEST_API
  properties: properties.TEST_API


PROPERTIES = {
  'gclient_config': Property(default='chromium'),
  'additional_repos': Property(default=None),
}

FAKE_DEP_REPO_URL = 'https://chromium.googlesource.com/chromium/src/fake-dep'
FAKE_DEP_REPO_REL_PATH = 'src/fake-dep'


@CONFIG_CTX(includes=['chromium'])
def fake_dep(c):
  c.repo_path_map[FAKE_DEP_REPO_URL] = (FAKE_DEP_REPO_REL_PATH, None)


def RunSteps(api: DEPS, gclient_config, additional_repos):
  api.gclient.set_config(gclient_config)
  api.gclient.c.target_os = ['android']
  with api.context(cwd=api.path.cache_dir / 'builder'):
    update_result = api.bot_update.ensure_checkout()
  api.chromium_android.run_tree_truth(update_result, additional_repos)


def GenTests(api: TEST_DEPS):

  def tree_truth_for_repos(check, steps, repo, *additional_repos):
    source_dir = api.path.cache_dir / 'builder/src'
    cmd = [
      str(source_dir / 'build/tree_truth.sh'),
      str(source_dir),
      repo,
      *additional_repos,
    ]
    return post_process.StepCommandEquals(check, steps, 'tree truth steps', cmd)

  yield api.test(
    'basic',
    api.chromium.ci_build(),
    api.post_check(tree_truth_for_repos, 'src'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'dependency-commit',
    api.chromium.ci_build(git_repo=FAKE_DEP_REPO_URL),
    api.properties(gclient_config='fake_dep'),
    api.post_check(tree_truth_for_repos, 'src', FAKE_DEP_REPO_REL_PATH),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'additional-repos',
    api.chromium.ci_build(),
    api.properties(additional_repos=['foo', 'bar']),
    api.post_check(tree_truth_for_repos, 'src', 'foo', 'bar'),
    api.post_process(post_process.DropExpectation),
  )
