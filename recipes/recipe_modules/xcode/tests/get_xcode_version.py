# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from PB.recipe_modules.build.xcode\
 import properties as xcode_properties

DEPS = [
    'xcode',
    'recipe_engine/properties',
    'recipe_engine/assertions',
    'recipe_engine/file',
    'recipe_engine/path',
]

from recipe_engine import post_process


def RunSteps(api):
  actual_version = api.xcode.get_xcode_version(
      api.path['cache'].join('builder'))
  api.assertions.assertEqual('0.0', actual_version)


def GenTests(api):
  config_path = 'some-path/test_xcode_config.json'
  xcode_input_properties = xcode_properties.InputProperties(
      xcode_config_path=config_path)
  yield api.test(
      'testing xcode version retrieval',
      api.path.exists(api.path['cache'].join('builder', config_path)),
      api.properties(**{'$build/xcode': xcode_input_properties}),
      api.post_process(post_process.StepSuccess,
                       'Read xcode_configs from repo'),
      api.post_process(post_process.DropExpectation),
  )
