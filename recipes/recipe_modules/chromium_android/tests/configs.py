# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process


DEPS = [
  'chromium',
  'chromium_android',
  'recipe_engine/properties',
]


def RunSteps(api):
  chromium_config_kwargs = {
      'BUILD_CONFIG': 'Debug',
      'TARGET_ARCH': 'arm',
      'TARGET_BITS': 32,
      'TARGET_PLATFORM': 'android',
  }
  chromium_config_kwargs.update(
      api.properties.get('chromium_config_kwargs', {}))

  api.chromium.set_config(
      api.properties['chromium_config'],
      BUILD_CONFIG='Debug',
      TARGET_ARCH='arm',
      TARGET_BITS=32,
      TARGET_PLATFORM='android',
  )


def GenTests(api):
  yield api.test(
      'cronet_builder',
      api.properties(chromium_config='cronet_builder'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'cronet_official',
      api.properties(chromium_config='cronet_official'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'main_builder',
      api.properties(chromium_config='main_builder'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'clang_builder',
      api.properties(chromium_config='clang_builder'),
      api.post_process(post_process.DropExpectation),
  )
