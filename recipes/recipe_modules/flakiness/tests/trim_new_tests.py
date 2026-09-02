# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process
from RECIPE_MODULES.build.flakiness.utils import TestDefinition
from PB.go.chromium.org.luci.buildbucket.proto import build as build_pb2
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb2

from PB.go.chromium.org.luci.resultdb.proto.v1 \
    import test_result as test_result_pb2

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import flakiness
from RECIPE_MODULES.recipe_engine import assertions, buildbucket, step


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  buildbucket: buildbucket.API
  flakiness: flakiness.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  flakiness: flakiness.TEST_API


def RunSteps(api: DEPS):

  def _generate_test_definition():
    test_result = test_result_pb2.TestResult(
        test_id='random_test_id',
        variant_hash='abc123',
        expected=False,
        status=test_result_pb2.FAIL,
    )
    return TestDefinition(test_result)

  new_tests = []
  for _ in range(15):
    new_tests.append(_generate_test_definition())

  filtered_test = api.flakiness.trim_new_tests(new_tests, 10)
  api.assertions.assertEqual(
      len(filtered_test), api.flakiness._max_test_targets)


def GenTests(api: TEST_DEPS):
  # max_test_targets defaults to 10
  yield api.test('basic', api.flakiness(check_for_flakiness=True,),
                 api.post_process(post_process.DropExpectation))
