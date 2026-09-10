# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium_3pp
from RECIPE_MODULES.recipe_engine import properties


@dataclass
class DEPS(RecipeScriptApi):
  chromium_3pp: chromium_3pp.API
  properties: properties.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  properties: properties.TEST_API


def RunSteps(api: DEPS):
  api.chromium_3pp.prepare()
  api.chromium_3pp.execute()


def GenTests(api: TEST_DEPS):
  yield api.test(
    'basic',
    api.properties(
      **{
        '$build/chromium_3pp': {
          'platform': 'linux-amd64',
          'package_prefix': 'chromium',
          'preprocess': [
            {
              'name': 'third_party/foo',
              'cmd': [
                '{CHECKOUT}/src/third_party/foo/bar.py',
                '--verbose',
              ],
            }
          ],
          'gclient_config': 'chromium',
          'gclient_apply_config': ['android'],
        }
      }
    ),
    api.post_process(
      post_process.MustRun,
      'Load all packages',
    ),
    api.post_process(
      post_process.MustRun,
      'Preprocessing third_party/foo',
    ),
    api.post_process(post_process.DropExpectation),
  )
