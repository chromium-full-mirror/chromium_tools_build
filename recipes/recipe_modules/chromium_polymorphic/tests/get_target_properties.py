# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from google.protobuf import json_format

from recipe_engine import post_process
from recipe_engine.engine_types import thaw
from recipe_engine.recipe_api import Property

from PB.go.chromium.org.luci.buildbucket.proto import (
  builder_common as builder_common_pb,
)
from PB.recipe_modules.build.chromium_polymorphic.properties import (
  BuilderGroupAndName,
  TesterFilter,
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
  'builder_id': Property(kind=dict),
  'tester_filter': Property(kind=dict, default=None),
  'expected_properties': Property(kind=dict),
}


def RunSteps(api: DEPS, builder_id, tester_filter, expected_properties):
  builder_id = builder_common_pb.BuilderID(**builder_id)
  tester_filter_proto = None
  if tester_filter:
    tester_filter_proto = TesterFilter()
    json_format.ParseDict(tester_filter, tester_filter_proto)
  properties = api.chromium_polymorphic.get_target_properties(
    builder_id, tester_filter=tester_filter_proto
  )
  expected_properties = thaw(expected_properties)
  api.assertions.assertEqual(properties, expected_properties)


def GenTests(api: TEST_DEPS):
  builder_id = {
    'project': 'fake-project',
    'bucket': 'fake-bucket',
    'builder': 'fake-builder',
  }

  yield api.test(
    'basic',
    api.chromium_polymorphic.properties_on_target_build(
      {
        'builder_group': 'fake-group',
        '$bootstrap/properties': 'fake-bootstrap-properties',
      }
    ),
    api.properties(
      builder_id=builder_id,
      expected_properties={
        '$build/chromium_polymorphic': {
          'target_builder_id': builder_id,
          'target_builder_group': 'fake-group',
        },
        '$bootstrap/properties': 'fake-bootstrap-properties',
      },
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'tester-filter',
    api.chromium_polymorphic.properties_on_target_build(
      {
        'builder_group': 'fake-group',
        '$bootstrap/properties': 'fake-bootstrap-properties',
      }
    ),
    api.properties(
      builder_id=builder_id,
      tester_filter=TesterFilter(
        testers=[
          BuilderGroupAndName(
            group='fake-group',
            builder='fake-tester',
          ),
        ]
      ),
      expected_properties={
        '$build/chromium_polymorphic': {
          'target_builder_id': builder_id,
          'target_builder_group': 'fake-group',
          'tester_filter': {
            'testers': [
              {
                'group': 'fake-group',
                'builder': 'fake-tester',
              }
            ],
          },
        },
        '$bootstrap/properties': 'fake-bootstrap-properties',
      },
    ),
    api.post_process(post_process.DropExpectation),
  )
