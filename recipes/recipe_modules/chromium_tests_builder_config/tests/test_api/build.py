# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Test recipe for the *_build methods of the test API."""

from __future__ import annotations

from recipe_engine import post_process

from RECIPE_MODULES.build import chromium_types
from RECIPE_MODULES.build.chromium_tests_builder_config import (
    builders, trybots, BuilderDatabase, BuilderSpec, TryDatabase, TrySpec)

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium, chromium_tests_builder_config
from RECIPE_MODULES.recipe_engine import assertions, platform, properties


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  chromium: chromium.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  platform: platform.API
  properties: properties.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  properties: properties.TEST_API

BUILDER_DB = BuilderDatabase.create({
    'fake-group': {
        'fake-builder':
            BuilderSpec.create(
                simulation_platform='mac',
                chromium_config_kwargs={'TARGET_BITS': 32}),
    },
})

TRY_DB = TryDatabase.create({
    'fake-try-group': {
        'fake-try-builder':
            TrySpec.create_for_single_mirror('fake-group', 'fake-builder'),
    },
})


def RunSteps(api: DEPS):
  api.assertions.assertEqual(api.chromium.get_builder_id(),
                             api.properties['expected_builder_id'])
  api.assertions.assertEqual(api.chromium_tests_builder_config.builder_db,
                             api.properties['expected_builder_db'])
  api.assertions.assertEqual(api.chromium_tests_builder_config.try_db,
                             api.properties['expected_try_db'])
  api.assertions.assertEqual(api.platform.name,
                             api.properties['expected_platform_name'])
  api.assertions.assertEqual(api.platform.bits,
                             api.properties['expected_platform_bits'])


def GenTests(api: TEST_DEPS):
  yield api.test(
      'generic-build',
      api.chromium_tests_builder_config.generic_build(
          builder_group='fake-group',
          builder='fake-builder',
          builder_db=BUILDER_DB),
      api.properties(
          expected_builder_id=chromium_types.BuilderId.create_for_group(
              'fake-group', 'fake-builder'),
          expected_builder_db=BUILDER_DB,
          expected_try_db=TryDatabase.create({}),
          expected_platform_name='mac',
          expected_platform_bits=32),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'generic-build-use-try-db',
      api.chromium_tests_builder_config.generic_build(
          builder_group='fake-group',
          builder='fake-builder',
          builder_db=BUILDER_DB,
          try_db=TRY_DB,
          use_try_db=True),
      api.properties(
          expected_builder_id=chromium_types.BuilderId.create_for_group(
              'fake-group', 'fake-builder'),
          expected_builder_db=BUILDER_DB,
          expected_try_db=TRY_DB,
          expected_platform_name='mac',
          expected_platform_bits=32),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'ci-build',
      api.chromium_tests_builder_config.ci_build(
          builder_group='fake-group',
          builder='fake-builder',
          builder_db=BUILDER_DB),
      api.properties(
          expected_builder_id=chromium_types.BuilderId.create_for_group(
              'fake-group', 'fake-builder'),
          expected_builder_db=BUILDER_DB,
          expected_try_db=TryDatabase.create({}),
          expected_platform_name='mac',
          expected_platform_bits=32),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'try-build',
      api.chromium_tests_builder_config.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
          builder_db=BUILDER_DB,
          try_db=TRY_DB),
      api.properties(
          expected_builder_id=chromium_types.BuilderId.create_for_group(
              'fake-try-group', 'fake-try-builder'),
          expected_builder_db=BUILDER_DB,
          expected_try_db=TRY_DB,
          expected_platform_name='mac',
          expected_platform_bits=32),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'default-db',
      api.chromium_tests_builder_config.generic_build(
          builder_group='migration.testing', builder='foo'),
      api.properties(
          expected_builder_id=chromium_types.BuilderId.create_for_group(
              'migration.testing', 'foo'),
          expected_builder_db=builders.BUILDERS,
          expected_try_db=trybots.TRYBOTS,
          expected_platform_name='linux',
          expected_platform_bits=64),
      api.post_process(post_process.DropExpectation),
  )
