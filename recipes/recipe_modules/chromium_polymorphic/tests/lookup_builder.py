# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process
from recipe_engine.recipe_api import Property

from RECIPE_MODULES.build import chromium_types
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

from PB.recipe_modules.build.chromium_polymorphic.properties import (
  BuilderGroupAndName,
)

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
  chromium_polymorphic,
  chromium_tests_builder_config,
)
from RECIPE_MODULES.recipe_engine import assertions, properties


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  chromium_polymorphic: chromium_polymorphic.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  properties: properties.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium_polymorphic: chromium_polymorphic.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  properties: properties.TEST_API


PROPERTIES = {
  'allow_tester': Property(default=False),
  'expected_builder_id': Property(default=None),
  'expected_tester_ids': Property(default=()),
}


def RunSteps(api: DEPS, allow_tester, expected_builder_id, expected_tester_ids):
  builder_id, builder_config = api.chromium_polymorphic.lookup_builder_config(
    allow_tester=allow_tester
  )
  api.assertions.assertEqual(builder_id, expected_builder_id)

  assert expected_builder_id
  expected_builder_ids = [expected_builder_id]
  api.assertions.assertCountEqual(
    builder_config.builder_ids, expected_builder_ids
  )

  expected_builder_ids_in_scope_for_testing = list(expected_builder_ids)
  expected_builder_ids_in_scope_for_testing.extend(expected_tester_ids)
  api.assertions.assertCountEqual(
    builder_config.builder_ids_in_scope_for_testing,
    expected_builder_ids_in_scope_for_testing,
  )


def GenTests(api: TEST_DEPS):
  ctbc_api = api.chromium_tests_builder_config

  yield api.test(
    'builder',
    api.chromium_polymorphic.triggered_properties(
      project='fake-project',
      bucket='fake-bucket',
      builder='fake-builder',
      builder_group='fake-group',
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_ci_builder(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .with_tester(
        builder_group='fake-group',
        builder='fake-tester-foo',
      )
      .with_tester(
        builder_group='fake-group',
        builder='fake-tester-bar',
      )
      .assemble()
    ),
    api.properties(
      expected_builder_id=chromium_types.BuilderId.create_for_group(
        'fake-group', 'fake-builder'
      ),
      expected_tester_ids=[
        chromium_types.BuilderId.create_for_group(
          'fake-group', 'fake-tester-foo'
        ),
        chromium_types.BuilderId.create_for_group(
          'fake-group', 'fake-tester-bar'
        ),
      ],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'tester-forbidden',
    api.chromium_polymorphic.triggered_properties(
      project='fake-project',
      bucket='fake-bucket',
      builder='fake-tester',
      builder_group='fake-group',
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_ci_tester(
        builder_group='fake-group',
        builder='fake-tester',
      )
      .with_parent(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.expect_exception('TesterForbidden'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'tester',
    api.chromium_polymorphic.triggered_properties(
      project='fake-project',
      bucket='fake-bucket',
      builder='fake-tester',
      builder_group='fake-group',
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_ci_tester(
        builder_group='fake-group',
        builder='fake-tester',
      )
      .with_parent(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.properties(
      allow_tester=True,
      expected_builder_id=chromium_types.BuilderId.create_for_group(
        'fake-group', 'fake-tester'
      ),
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'tester-filter',
    api.chromium_polymorphic.triggered_properties(
      project='fake-project',
      bucket='fake-bucket',
      builder='fake-builder',
      builder_group='fake-group',
      testers=[
        BuilderGroupAndName(group='fake-group', builder='fake-tester-foo'),
      ],
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_ci_builder(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .with_tester(
        builder_group='fake-group',
        builder='fake-tester-foo',
      )
      .with_tester(
        builder_group='fake-group',
        builder='fake-tester-bar',
      )
      .assemble()
    ),
    api.properties(
      expected_builder_id=chromium_types.BuilderId.create_for_group(
        'fake-group', 'fake-builder'
      ),
      expected_tester_ids=[
        chromium_types.BuilderId.create_for_group(
          'fake-group', 'fake-tester-foo'
        )
      ],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'legacy-config-builder',
    api.chromium_polymorphic.triggered_properties(
      project='fake-project',
      bucket='fake-bucket',
      builder='fake-builder',
      builder_group='fake-group',
    ),
    ctbc_api.databases(
      builder_db=ctbc.BuilderDatabase.create(
        {
          'fake-group': {
            'fake-builder': ctbc.BuilderSpec.create(
              gclient_config='chromium',
              chromium_config='chromium',
            ),
          },
        }
      )
    ),
    api.properties(
      expected_builder_id=chromium_types.BuilderId.create_for_group(
        'fake-group', 'fake-builder'
      )
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'legacy-config-tester',
    api.chromium_polymorphic.triggered_properties(
      project='fake-project',
      bucket='fake-bucket',
      builder='fake-tester',
      builder_group='fake-group',
    ),
    ctbc_api.databases(
      builder_db=ctbc.BuilderDatabase.create(
        {
          'fake-group': {
            'fake-builder': ctbc.BuilderSpec.create(
              gclient_config='chromium',
              chromium_config='chromium',
            ),
            'fake-tester': ctbc.BuilderSpec.create(
              execution_mode=ctbc.TEST,
              parent_buildername='fake-builder',
              gclient_config='chromium',
              chromium_config='chromium',
            ),
          },
        }
      )
    ),
    api.properties(
      allow_tester=True,
      expected_builder_id=chromium_types.BuilderId.create_for_group(
        'fake-group', 'fake-tester'
      ),
    ),
    api.post_process(post_process.DropExpectation),
  )
