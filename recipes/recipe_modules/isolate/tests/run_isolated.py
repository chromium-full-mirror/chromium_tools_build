# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process
from recipe_engine.recipe_api import Property

from RECIPE_MODULES.build.chromium_tests import resultdb

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import isolate
from RECIPE_MODULES.recipe_engine import buildbucket, properties


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  isolate: isolate.API
  properties: properties.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  properties: properties.TEST_API

PROPERTIES = {
    'env': Property(kind=dict, default=None),
    'resultdb': Property(kind=resultdb.ResultDB, default=None),
}


def RunSteps(
    api: DEPS,
    env,
    resultdb,  # pylint: disable=redefined-outer-name
):
  api.isolate.run_isolated(
      'run_isolated',
      'isolate_hash',
      ['some', 'args'],
      env=env,
      resultdb=resultdb,
  )


def GenTests(api: TEST_DEPS):
  yield api.test(
      'basic',
      api.post_process(post_process.StepCommandContains, 'run_isolated', [
          'isolate_hash',
          '--',
          'some',
          'args',
      ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'env',
      api.properties(env={'VAR': 'value'}),
      api.post_check(lambda check, steps: \
        check(['--env', 'VAR=value'] in steps['run_isolated'].cmd)),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'rdb',
      api.properties(resultdb=resultdb.ResultDB.create()),
      # Create a buildbucket build so that the luci context is initialized with
      # resultdb
      api.buildbucket.generic_build(),
      api.post_check(lambda check, steps: \
        check(['rdb', 'stream', ..., '--', 'vpython3']
              in steps['run_isolated'].cmd)),
      api.post_process(post_process.DropExpectation),
  )
