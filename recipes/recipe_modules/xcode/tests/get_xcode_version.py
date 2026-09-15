# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.recipe_modules.build.xcode import properties as xcode_properties

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import xcode
from RECIPE_MODULES.recipe_engine import (
  assertions,
  file,
  path,
  properties,
)


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  file: file.API
  path: path.API
  properties: properties.API
  xcode: xcode.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  path: path.TEST_API
  properties: properties.TEST_API


from recipe_engine import post_process


def RunSteps(api: DEPS):
  actual_version = api.xcode.get_xcode_version(api.path.cache_dir / 'builder')
  api.assertions.assertEqual('123ABC', actual_version)


def GenTests(api: TEST_DEPS):
  config_path = 'some-path/test_xcode_config.json'
  xcode_input_properties = xcode_properties.InputProperties(
    xcode_config_path=config_path
  )
  yield api.test(
    'testing xcode version retrieval',
    api.path.exists(api.path.cache_dir.joinpath('builder', config_path)),
    api.properties(**{'$build/xcode': xcode_input_properties}),
    api.post_process(post_process.StepSuccess, 'Read xcode_configs from repo'),
    api.post_process(post_process.DropExpectation),
  )
