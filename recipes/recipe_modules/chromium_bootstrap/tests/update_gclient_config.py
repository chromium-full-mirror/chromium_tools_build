# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from PB.go.chromium.org.luci.buildbucket.proto import common

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium_bootstrap
from RECIPE_MODULES.depot_tools import gclient
from RECIPE_MODULES.recipe_engine import assertions, properties


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  chromium_bootstrap: chromium_bootstrap.API
  gclient: gclient.API
  properties: properties.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium_bootstrap: chromium_bootstrap.TEST_API
  properties: properties.TEST_API


DEFAULT_REPO = 'fake-repo'
DEFAULT_HASH = 'default-hash'


def RunSteps(api: DEPS):
  gclient_config = api.gclient.make_config()
  for repo, path in api.properties.get('paths_by_repo', {}).items():
    s = gclient_config.solutions.add()
    s.name = path
    s.url = repo
    gclient_config.repo_path_map[repo] = (path, 'HEAD')

  assert not gclient_config.revisions

  with api.chromium_bootstrap.update_gclient_config(gclient_config) as callback:
    api.assertions.assertEqual(
      gclient_config.revisions, api.properties['expected_revisions']
    )

    if not api.properties.get('skip_callback', False):
      manifest = api.properties['manifest']
      callback(manifest)


def GenTests(api: TEST_DEPS):

  def paths_by_repo(paths_by_repo):
    return api.properties(paths_by_repo=paths_by_repo)

  def manifest(repo_and_revision_by_path):
    manifest = {
      path: {
        'repository': f'{repo}.git',
        'revision': revision,
      }
      for path, (repo, revision) in repo_and_revision_by_path.items()
    }
    return api.properties(manifest=manifest)

  def expect_gclient_config_revisions(revisions):
    return api.properties(expected_revisions=revisions)

  yield api.test(
    'not-bootstrapped',
    manifest({}),
    expect_gclient_config_revisions({}),
    api.post_process(post_process.DropExpectation),
  )

  commits = [
    common.GitilesCommit(
      host='chromium.googlesource.com',
      project='chromium/src',
      ref='refs/heads/main',
      id='src-hash',
    ),
    common.GitilesCommit(
      host='chrome-internal.googlesource.com',
      project='chrome/src-internal',
      ref='refs/heads/main',
      id='src-internal-hash',
    ),
  ]

  yield api.test(
    'bootstrapped',
    api.chromium_bootstrap.properties(commits=commits),
    paths_by_repo(
      {
        'https://chromium.googlesource.com/chromium/src': 'src',
        'https://chrome-internal.googlesource.com/chrome/src-internal': 'src-internal',
      }
    ),
    manifest(
      {
        'src': ('https://chromium.googlesource.com/chromium/src', 'src-hash'),
        'src-internal': (
          'https://chrome-internal.googlesource.com/chrome/src-internal',
          'src-internal-hash',
        ),
      }
    ),
    expect_gclient_config_revisions(
      {
        'src': 'src-hash',
        'src-internal': 'src-internal-hash',
      }
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'bootstrapped-does-not-checkout-repo-with-missing-repo-path-map-entry',
    api.chromium_bootstrap.properties(commits=commits),
    paths_by_repo(
      {
        'https://chromium.googlesource.com/chromium/src': 'src',
      }
    ),
    manifest(
      {
        'src': ('https://chromium.googlesource.com/chromium/src', 'src-hash'),
      }
    ),
    expect_gclient_config_revisions({'src': 'src-hash'}),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'bootstrapped-checks-out-repo-with-missing-repo-path-map-entry',
    api.chromium_bootstrap.properties(commits=commits),
    paths_by_repo(
      {
        'https://chromium.googlesource.com/chromium/src': 'src',
      }
    ),
    manifest(
      {
        'src': ('https://chromium.googlesource.com/chromium/src', 'src-hash'),
        'src-internal': (
          'https://chrome-internal.googlesource.com/chrome/src-internal',
          'src-internal-hash',
        ),
      }
    ),
    expect_gclient_config_revisions({'src': 'src-hash'}),
    api.expect_status('INFRA_FAILURE'),
    api.post_check(
      post_process.SummaryMarkdownRE,
      (
        r'https://chrome-internal\.googlesource\.com/chrome/src-internal'
        " does not appear in the gclient config's repo_path_map"
      ),
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'skip-callback',
    expect_gclient_config_revisions({}),
    api.properties(skip_callback=True),
    api.expect_status('INFRA_FAILURE'),
    api.post_check(
      post_process.SummaryMarkdownRE,
      (
        'The callback from update_gclient_config'
        ' must be called with the manifest from bot_update'
      ),
    ),
    api.post_process(post_process.DropExpectation),
  )

  commits = [
    common.GitilesCommit(
      host='chromium.googlesource.com',
      project='chromium/src',
      ref='refs/heads/main',
    ),
    common.GitilesCommit(
      host='chrome-internal.googlesource.com',
      project='chrome/src-internal',
      ref='refs/heads/main',
      id='src-internal-hash',
    ),
  ]

  yield api.test(
    'bootstrapped-commit-without-id',
    api.chromium_bootstrap.properties(commits=commits),
    paths_by_repo(
      {
        'https://chromium.googlesource.com/chromium/src': 'src',
        'https://chrome-internal.googlesource.com/chrome/src-internal': 'src-internal',
      }
    ),
    manifest(
      {
        'src': ('https://chromium.googlesource.com/chromium/src', 'src-hash'),
        'src-internal': (
          'https://chrome-internal.googlesource.com/chrome/src-internal',
          'src-internal-hash',
        ),
      }
    ),
    expect_gclient_config_revisions(
      {
        'src': 'refs/heads/main',
        'src-internal': 'src-internal-hash',
      }
    ),
    api.post_process(post_process.DropExpectation),
  )
