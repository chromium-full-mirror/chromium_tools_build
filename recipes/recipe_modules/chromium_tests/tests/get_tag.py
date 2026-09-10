# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Test to ensure the correctness of get_first_tag"""

from __future__ import annotations

from recipe_engine import post_process

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb2

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium_tests
from RECIPE_MODULES.recipe_engine import buildbucket, properties


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  chromium_tests: chromium_tests.API
  properties: properties.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  properties: properties.TEST_API


def RunSteps(api: DEPS):
  result = api.chromium_tests.get_first_tag('lookup_key')
  expected = api.properties.get('result')
  assert result == expected


def GenTests(api: TEST_DEPS):
  yield api.test(
    'basic',
    api.buildbucket.try_build(
      tags=[common_pb2.StringPair(key='lookup_key', value='recipe_tests')]
    ),
    api.properties(result='recipe_tests'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'not found',
    api.buildbucket.try_build(
      tags=[common_pb2.StringPair(key='not_lookup_key', value='recipe_tests')]
    ),
    api.properties(result=None),
    api.post_process(post_process.DropExpectation),
  )
