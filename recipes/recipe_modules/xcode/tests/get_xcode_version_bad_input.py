# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from PB.recipe_modules.build.xcode\
  import properties as xcode_properties

from recipe_engine.post_process import (DropExpectation)

DEPS = [
    'xcode',
    'recipe_engine/properties',
    'recipe_engine/assertions',
    'recipe_engine/file',
    'recipe_engine/path',
]


def RunSteps(api):
  actual_version = api.xcode.get_xcode_version(api.path.cache_dir / 'builder')
  api.assertions.assertIsNone(actual_version)


def GenTests(api):
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
