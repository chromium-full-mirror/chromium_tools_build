# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import angle
from RECIPE_MODULES.depot_tools import gclient
from RECIPE_MODULES.recipe_engine import properties


@dataclass
class DEPS(RecipeScriptApi):
  angle: angle.API
  gclient: gclient.API
  properties: properties.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  properties: properties.TEST_API

TEST_CONFIGS = [
    'angle',
]


def RunSteps(api: DEPS):
  for config_name in TEST_CONFIGS:
    api.gclient.make_config(config_name)

  api.gclient.set_config('angle')
  api.gclient.apply_config(api.properties.get('apply_gclient_config'))


def GenTests(api: TEST_DEPS):
  yield api.test(
      'angle_android',
      api.properties(apply_gclient_config='angle_android'),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'angle_nointernal',
      api.properties(apply_gclient_config='angle_nointernal'),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'angle_no_extra_traces',
      api.properties(apply_gclient_config='angle_no_extra_traces'),
      api.post_process(post_process.DropExpectation),
  )