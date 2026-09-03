# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import binary_size
from RECIPE_MODULES.recipe_engine import properties


@dataclass
class DEPS(RecipeScriptApi):
  binary_size: binary_size.API
  properties: properties.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  binary_size: binary_size.TEST_API


def RunSteps(api: DEPS):
  return api.binary_size.android_binary_size(
      chromium_config='chromium',
      chromium_apply_configs=['mb'],
      gclient_config='chromium',
      gclient_apply_configs=['android'],
      try_gs_analysis=True)


def GenTests(api: TEST_DEPS):
  yield api.test('basic', api.binary_size.build(override_commit_log=True),
                 api.post_process(post_process.DropExpectation))
