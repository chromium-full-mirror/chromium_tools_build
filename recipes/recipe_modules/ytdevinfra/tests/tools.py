# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import recipe_api
from recipe_engine.post_process import StepCommandRE, DropExpectation
from recipe_engine.recipe_api import Property

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import ytdevinfra
from RECIPE_MODULES.recipe_engine import properties


@dataclass
class DEPS(RecipeScriptApi):
  properties: properties.API
  ytdevinfra: ytdevinfra.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  properties: properties.TEST_API


PROPERTIES = {
  'tool': Property(kind=str, default='ytdevinfra_android'),
  'tool_target': Property(kind=str, default=None),
}


def RunSteps(api: recipe_api.RecipeApi, tool: str, tool_target: str | None):
  if not tool_target:
    api.ytdevinfra.set_config(tool)
  else:
    api.ytdevinfra.set_config(tool, TARGET=tool_target)
  api.ytdevinfra.title()


def GenTests(api: TEST_DEPS):
  yield api.test(
    'droid',
    api.properties(ytdevinfra_recipe_version=0.2),
    api.post_process(
      StepCommandRE,
      'Print title from API module',
      ['echo', 'Recipe for building'],
    ),
    api.post_process(DropExpectation),
  )
  yield api.test(
    'droid-0.1',
    api.properties(ytdevinfra_recipe_version=0.1, tool='ytdevinfra_android'),
    api.post_process(
      StepCommandRE,
      'Print title from API module',
      ['echo', 'Recipe for building'],
    ),
    api.post_process(DropExpectation),
  )

  yield api.test(
    'linux-tv',
    api.properties(
      ytdevinfra_recipe_version=0.2, tool='ytdevinfra_tv', tool_target='linux'
    ),
    api.post_process(
      StepCommandRE,
      'Print title from API module',
      ['echo', 'Recipe for building'],
    ),
    api.post_process(DropExpectation),
  )
  yield api.test(
    'linux-tv-0.1',
    api.properties(
      ytdevinfra_recipe_version=0.1, tool='ytdevinfra_tv', tool_target='linux'
    ),
    api.post_process(
      StepCommandRE,
      'Print title from API module',
      ['echo', 'Recipe for building'],
    ),
    api.post_process(DropExpectation),
  )

  yield api.test(
    'tv',
    api.expect_exception('BadConf'),
    api.properties(
      ytdevinfra_recipe_version=0.2,
      tool='ytdevinfra_tv',
      tool_target='AndroidAPK',
    ),
    api.post_process(DropExpectation),
  )
  yield api.test(
    'tv-0.1',
    api.expect_exception('BadConf'),
    api.properties(
      ytdevinfra_recipe_version=0.1,
      tool='ytdevinfra_tv',
      tool_target='AndroidAPK',
    ),
    api.post_process(DropExpectation),
  )
