# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""
Recipe for running presubmit in V8 CI.
"""

from recipe_engine.post_process import Filter

DEPS = [
    'chromium',
    'depot_tools/gclient',
    'recipe_engine/context',
    'recipe_engine/properties',
    'v8',
]

def RunSteps(api):
  api.gclient.set_config('v8')
  api.chromium.set_config('v8')
  update_result = api.v8.checkout()
  api.v8.runhooks()
  source_dir = update_result.source_root.path
  with api.context(
      cwd=source_dir, env_prefixes={'PATH': [api.v8.depot_tools_path]}):
    api.v8.vpython(
        'Presubmit',
        source_dir.joinpath('tools', 'v8_presubmit.py'),
        ['--no-linter-cache'],
        wrapper=('rdb', 'stream', '--'),
    )


def GenTests(api):
  yield api.test(
      'basic',
      api.post_process(Filter('Presubmit')),
      status='SUCCESS',
  )
