# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

from RECIPE_MODULES.build.chromium_tests.steps import MockTestSpec

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium_rts, chromium_tests
from RECIPE_MODULES.recipe_engine import file, path, properties


@dataclass
class DEPS(RecipeScriptApi):
  chromium_rts: chromium_rts.API
  chromium_tests: chromium_tests.API
  file: file.API
  path: path.API
  properties: properties.API


def RunSteps(api: DEPS):
  spec = MockTestSpec.create(
      name='blink_web_tests',
      runs_on_swarming=True,
      enable_rts_filtering=True,
  )
  test = spec.get_test(api.chromium_tests)
  messages = []
  api.chromium_rts.append_test_step_text(test, messages)
  assert not messages

  api.chromium_rts.set_swarming_test_execution_info(
      test, {'rts': {
          'blink_web_tests': ['/bin/rts_cmd']
      }})
  assert test.raw_cmd == ['/bin/rts_cmd']
  api.chromium_rts.append_test_step_text(test, messages)
  assert messages == [
      'Ran tests selected by Regression Test Selection (RTS).\n'
  ]
  api.chromium_rts.set_swarming_test_execution_info(test, None)

  # Test non-allowlisted suite
  spec2 = MockTestSpec.create(name='other_tests', runs_on_swarming=True)
  test2 = spec2.get_test(api.chromium_tests)
  api.chromium_rts.set_swarming_test_execution_info(
      test2, {'rts': {
          'other_tests': ['/bin/rts_cmd']
      }})
  assert test2.raw_cmd != ['/bin/rts_cmd']


def GenTests(api: RecipeTestApi):
  yield api.test(
      'set_swarming_test_execution_info',
      api.post_process(post_process.DropExpectation),
  )
