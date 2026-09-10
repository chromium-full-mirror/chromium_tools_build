# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process
from recipe_engine.config import BadConf
from recipe_engine.recipe_api import Property

from RECIPE_MODULES.build.chromium.config import config_ctx

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium
from RECIPE_MODULES.recipe_engine import assertions, platform, properties


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  chromium: chromium.API
  platform: platform.API
  properties: properties.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  platform: platform.TEST_API
  properties: properties.TEST_API


PROPERTIES = {
  'config': Property(kind=str, default=''),
  'host_platform': Property(kind=str, default=''),
  'target_platform': Property(kind=str, default='linux'),
}


@config_ctx()
def bad_generator(c):
  c.project_generator.tool = 'this is not a valid generator'


def RunSteps(api: DEPS, config, host_platform, target_platform):
  with api.assertions.assertRaises(BadConf):
    api.chromium.set_config(
      config,
      HOST_PLATFORM=host_platform,
      TARGET_PLATFORM=target_platform,
    )


def GenTests(api: TEST_DEPS):
  yield api.test(
    'bad_generator',
    api.properties(config='bad_generator'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'bad_host_platform',
    api.properties(config='chromium', host_platform='linux'),
    api.platform('win', 64),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'cross_compile_without_host_platform_set_explicitly',
    api.properties(config='chromium', target_platform='win'),
    api.platform('linux', 64),
    api.post_process(post_process.DropExpectation),
  )
