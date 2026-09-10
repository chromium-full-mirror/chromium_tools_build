# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium, chromium_android
from RECIPE_MODULES.recipe_engine import properties


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_android: chromium_android.API
  properties: properties.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  properties: properties.TEST_API


def RunSteps(api: DEPS):
  chromium_config_kwargs = {
    'BUILD_CONFIG': 'Debug',
    'TARGET_ARCH': 'arm',
    'TARGET_BITS': 32,
    'TARGET_PLATFORM': 'android',
  }
  chromium_config_kwargs.update(
    api.properties.get('chromium_config_kwargs', {})
  )

  api.chromium.set_config(
    api.properties['chromium_config'],
    BUILD_CONFIG='Debug',
    TARGET_ARCH='arm',
    TARGET_BITS=32,
    TARGET_PLATFORM='android',
  )


def GenTests(api: TEST_DEPS):
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
