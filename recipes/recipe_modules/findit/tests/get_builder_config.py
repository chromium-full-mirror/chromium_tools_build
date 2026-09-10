# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from RECIPE_MODULES.build import chromium_types
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium_tests_builder_config, findit
from RECIPE_MODULES.recipe_engine import assertions, properties


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  findit: findit.API
  properties: properties.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  properties: properties.TEST_API


def RunSteps(api: DEPS):
  target_builder_id = api.properties['target_builder_id']

  builder_config = api.findit.get_builder_config(target_builder_id)

  api.assertions.assertCountEqual(
    builder_config.builder_ids, api.properties['builder_ids']
  )
  api.assertions.assertCountEqual(
    builder_config.builder_ids_in_scope_for_testing,
    api.properties['builder_ids_in_scope_for_testing'],
  )

  if 'targets_spec_directory' in api.properties:
    api.assertions.assertEqual(
      builder_config.targets_spec_directory,
      api.properties['targets_spec_directory'],
    )


def GenTests(api: TEST_DEPS):
  builder_id = chromium_types.BuilderId.create_for_group(
    'fake-group', 'fake-builder'
  )
  tester_id = chromium_types.BuilderId.create_for_group(
    'fake-group', 'fake-tester'
  )

  yield api.test(
    'src-side-builder',
    api.properties(
      target_builder_id=builder_id,
      builder_ids=[builder_id],
      builder_ids_in_scope_for_testing=[builder_id],
    ),
    api.chromium_tests_builder_config.properties(
      api.chromium_tests_builder_config.properties_assembler_for_ci_builder(
        builder_group=builder_id.group,
        builder=builder_id.builder,
      ).assemble()
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'src-side-tester',
    api.properties(
      target_builder_id=tester_id,
      builder_ids=[builder_id],
      builder_ids_in_scope_for_testing=[builder_id, tester_id],
      targets_spec_directory='fake-targets-spec-directory',
    ),
    api.chromium_tests_builder_config.properties(
      api.chromium_tests_builder_config.properties_assembler_for_ci_tester(
        builder_group=tester_id.group,
        builder=tester_id.builder,
      )
      .with_parent(
        builder_group=builder_id.group,
        builder=builder_id.builder,
      )
      .with_targets_spec_directory('fake-targets-spec-directory')
      .assemble()
    ),
    api.post_process(post_process.DropExpectation),
  )

  # Target builder refers to builder other than what the src-side config has
  yield api.test(
    'bad-src-side-builder',
    api.properties(
      target_builder_id=chromium_types.BuilderId.create_for_group(
        'fake-group', 'other-fake-builder'
      )
    ),
    api.chromium_tests_builder_config.properties(
      api.chromium_tests_builder_config.properties_assembler_for_ci_builder(
        builder_group=builder_id.group,
        builder=builder_id.builder,
      ).assemble()
    ),
    api.post_check(post_process.StepException, 'invalid target builder'),
    api.expect_status('INFRA_FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'non-src-side-builder',
    api.properties(
      target_builder_id=builder_id,
      builder_ids=[builder_id],
      builder_ids_in_scope_for_testing=[builder_id],
    ),
    api.chromium_tests_builder_config.databases(
      ctbc.BuilderDatabase.create(
        {
          builder_id.group: {
            builder_id.builder: ctbc.BuilderSpec.create(
              gclient_config='chromium',
              chromium_config='chromium',
            ),
          },
        }
      )
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'non-src-side-tester',
    api.properties(
      target_builder_id=tester_id,
      builder_ids=[builder_id],
      builder_ids_in_scope_for_testing=[builder_id, tester_id],
    ),
    api.chromium_tests_builder_config.databases(
      ctbc.BuilderDatabase.create(
        {
          builder_id.group: {
            builder_id.builder: ctbc.BuilderSpec.create(
              gclient_config='chromium',
              chromium_config='chromium',
            ),
            tester_id.builder: ctbc.BuilderSpec.create(
              execution_mode=ctbc.TEST,
              parent_buildername=builder_id.builder,
              gclient_config='chromium',
              chromium_config='chromium',
            ),
            'other-fake-tester': ctbc.BuilderSpec.create(
              execution_mode=ctbc.TEST,
              parent_buildername=builder_id.builder,
              gclient_config='chromium',
              chromium_config='chromium',
            ),
          },
        }
      )
    ),
    api.post_process(post_process.DropExpectation),
  )
