# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""
Recipe for running presubmit in V8 CI.
"""

from recipe_engine.post_process import Filter

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium, v8
from RECIPE_MODULES.depot_tools import gclient
from RECIPE_MODULES.recipe_engine import context, properties


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  context: context.API
  gclient: gclient.API
  properties: properties.API
  v8: v8.API


def RunSteps(api: DEPS):
  api.gclient.set_config('v8')
  api.chromium.set_config('v8')
  update_result = api.v8.checkout()
  source_dir = update_result.source_root.path
  build_dir = api.v8.build_dir(source_dir)
  api.v8.runhooks(source_dir, build_dir)
  with api.context(
      cwd=source_dir,
      env_prefixes={'PATH': [api.v8.depot_tools_path(source_dir)]}):
    api.v8.vpython(
        'Presubmit',
        source_dir.joinpath('tools', 'v8_presubmit.py'),
        ['--no-linter-cache'],
        wrapper=('rdb', 'stream', '--'),
    )


def GenTests(api: RecipeTestApi):
  yield api.test(
      'basic',
      api.post_process(Filter('Presubmit')),
      status='SUCCESS',
  )
