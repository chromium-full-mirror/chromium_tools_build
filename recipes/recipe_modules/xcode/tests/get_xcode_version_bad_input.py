# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.recipe_modules.build.xcode\
  import properties as xcode_properties

from recipe_engine.post_process import (DropExpectation)

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
  properties: properties.TEST_API


def RunSteps(api: DEPS):
  actual_version = api.xcode.get_xcode_version(api.path.cache_dir / 'builder')
  api.assertions.assertIsNone(actual_version)


def GenTests(api: TEST_DEPS):
  yield api.test(
      'testing xcode version retrieval with no input',
      api.post_process(DropExpectation),
  )

  config_path = 'some-path/test_xcode_config.json'
  xcode_input_properties = xcode_properties.InputProperties(
      xcode_config_path=config_path)
  yield api.test(
      'testing xcode version when file does not exist',
      api.properties(**{'$build/xcode': xcode_input_properties}),
      api.post_process(DropExpectation),
  )
