# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium, chromium_tests, chromium_tests_builder_config
from RECIPE_MODULES.recipe_engine import assertions, platform, properties


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  chromium: chromium.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  platform: platform.API
  properties: properties.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  platform: platform.TEST_API
  properties: properties.TEST_API


def RunSteps(api: DEPS):
  builder_id, builder_config = (
      api.chromium_tests_builder_config.lookup_builder())
  api.chromium_tests.configure_build(builder_config)
  actual = api.chromium_tests._get_builders_to_trigger(builder_id,
                                                       builder_config)
  expected = api.properties['expected']
  api.assertions.assertCountEqual(actual, expected)


def GenTests(api: TEST_DEPS):
  ctbc_api = api.chromium_tests_builder_config

  yield api.test(
      'basic',
      api.platform('linux', 64),
      api.chromium.ci_build(
          builder_group='fake-group',
          builder='fake-builder',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).with_tester(
              builder_group='fake-group',
              builder='fake-tester',
          ).assemble()),
      api.properties(expected=['fake-tester']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'dedup',
      api.chromium_tests_builder_config.ci_build(
          builder_group='fake-group',
          builder='fake-builder',
          # Multiple entries for 'fake-tester' in different builder
          # groups, as would be the case when making a copy for changing
          # the builder group
          builder_db=ctbc.BuilderDatabase.create({
              'fake-group': {
                  'fake-builder':
                      ctbc.BuilderSpec.create(
                          chromium_config='chromium',
                          gclient_config='chromium',
                      ),
                  'fake-tester':
                      ctbc.BuilderSpec.create(
                          execution_mode=ctbc.TEST,
                          parent_buildername='fake-builder',
                          chromium_config='chromium',
                          gclient_config='chromium',
                      ),
              },
              'fake-group2': {
                  'fake-tester':
                      ctbc.BuilderSpec.create(
                          execution_mode=ctbc.TEST,
                          parent_builder_group='fake-group',
                          parent_buildername='fake-builder',
                          chromium_config='chromium',
                          gclient_config='chromium',
                      ),
              },
          })),
      api.properties(expected=['fake-tester']),
      api.post_process(post_process.DropExpectation),
  )
