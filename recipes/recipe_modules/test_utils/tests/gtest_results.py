# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import itertools

from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import test_utils
from RECIPE_MODULES.recipe_engine import json, properties, step


@dataclass
class DEPS(RecipeScriptApi):
  json: json.API
  properties: properties.API
  step: step.API
  test_utils: test_utils.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  json: json.TEST_API
  test_utils: test_utils.TEST_API


def RunSteps(api: DEPS):
  api.step('fake_test',
           ['fake', '--gtest-results', api.test_utils.gtest_results()])


def GenTests(api: TEST_DEPS):
  yield api.test(
      'many-log-lines',
      api.override_step_data(
          'fake_test',
          api.test_utils.gtest_results(
              api.json.dumps({
                  'per_iteration_data': [{
                      'SpammyTest': [{
                          'elapsed_time_ms':
                              1000,
                          'output_snippet':
                              '\n'.join(itertools.repeat('line', 10000)),
                          'status':
                              'SUCCESS',
                      }],
                  }],
              }))),
      api.post_process(post_process.DropExpectation),
  )
