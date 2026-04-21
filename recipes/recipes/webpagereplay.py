# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""
Recipe for running go tests in https://chromium.googlesource.com/webpagereplay.
"""

DEPS = [
    'depot_tools/bot_update',
    'depot_tools/gclient',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/golang',
    'recipe_engine/path',
    'recipe_engine/step',
]


def RunSteps(api):
  config = api.gclient.make_config()
  solution = config.solutions.add()
  solution.name = 'webpagereplay'
  solution.url = 'https://chromium.googlesource.com/webpagereplay'
  solution.revision = api.buildbucket.gitiles_commit.id or 'HEAD'
  api.gclient.c = config
  update_result = api.bot_update.ensure_checkout()
  root_dir = update_result.source_root.path
  go_version = '1.26.2'
  with api.golang(version=go_version), api.context(cwd=root_dir):
    api.step('check go format', [root_dir / 'scripts' / 'check_go_format.py'])
    src_dir = root_dir / 'src'
    with api.context(cwd=src_dir):
      api.step('test webpagereplay', ['go', 'test', './webpagereplay'])
      api.step('test wpr.go', ['go', 'test', 'wpr.go', 'wpr_test.go'])
      api.step('test httparchive.go',
               ['go', 'test', 'httparchive.go', 'httparchive_test.go'])


def GenTests(api):
  yield api.test('basic')
