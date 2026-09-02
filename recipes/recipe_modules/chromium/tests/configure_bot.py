# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from RECIPE_MODULES.build import chromium_types


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium
from RECIPE_MODULES.recipe_engine import properties


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  properties: properties.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API


_BUILDERS_DICT = {
    'test_group': {
        'builders': {
            'test_buildername':
                chromium_types.BuilderSpec.create(
                    chromium_config='chromium_clang',
                    chromium_apply_config=['mb'],
                    gclient_apply_config=['android'],
                ),
        },
    },
    'tryserver_test': {
        'builders': {
            'mac_trybot':
                chromium_types.BuilderSpec.create(
                    chromium_config='chromium_clang',),
            'win_trybot':
                chromium_types.BuilderSpec.create(
                    chromium_config='chromium_clang',),
        },
    },
}


def RunSteps(api: DEPS):
  api.chromium.configure_bot(_BUILDERS_DICT, additional_configs=['codesearch'])


def GenTests(api: TEST_DEPS):
  for test in api.chromium.gen_tests_for_builders(_BUILDERS_DICT):
    yield test + api.post_process(post_process.DropExpectation)
