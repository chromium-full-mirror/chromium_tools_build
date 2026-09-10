# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process
from recipe_engine.recipe_api import Property

from PB.go.chromium.org.luci.buildbucket.proto import (
  builder_common as builder_common_pb,
)

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium_polymorphic
from RECIPE_MODULES.recipe_engine import assertions, properties


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  chromium_polymorphic: chromium_polymorphic.API
  properties: properties.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium_polymorphic: chromium_polymorphic.TEST_API
  properties: properties.TEST_API


PROPERTIES = {
  'expected': Property(),
}


def RunSteps(api: DEPS, expected):
  target_builder_id = api.chromium_polymorphic.target_builder_id
  expected = builder_common_pb.BuilderID(**expected)
  api.assertions.assertEqual(target_builder_id, expected)


def GenTests(api: TEST_DEPS):
  yield api.test(
    'basic',
    api.chromium_polymorphic.triggered_properties(
      project='fake-project',
      bucket='fake-bucket',
      builder='fake-builder',
      builder_group='fake-group',
    ),
    api.properties(
      expected=dict(
        project='fake-project',
        bucket='fake-bucket',
        builder='fake-builder',
      )
    ),
    api.post_process(post_process.DropExpectation),
  )
