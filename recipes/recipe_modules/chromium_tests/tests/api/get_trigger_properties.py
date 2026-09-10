# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import engine_types
from recipe_engine import post_process

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc
from RECIPE_MODULES.depot_tools.gclient import CONFIG_CTX

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
  chromium,
  chromium_tests,
  chromium_tests_builder_config,
)
from RECIPE_MODULES.depot_tools import bot_update
from RECIPE_MODULES.recipe_engine import assertions, json, properties


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  bot_update: bot_update.API
  chromium: chromium.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  json: json.API
  properties: properties.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  bot_update: bot_update.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  properties: properties.TEST_API


@CONFIG_CTX()
def override_foo(c):
  c.revisions['src/foo'] = 'HEAD'


def RunSteps(api: DEPS):
  builder_id = api.chromium.get_builder_id()
  builder_id, builder_config = (
    api.chromium_tests_builder_config.lookup_builder()
  )
  api.chromium_tests.configure_build(builder_config)
  update_step, _, _ = api.chromium_tests.prepare_checkout(builder_config)
  properties = api.chromium_tests._get_trigger_properties(
    builder_id, update_step
  )
  expected = engine_types.thaw(api.properties['expected_trigger_properties'])
  for k, v in expected.items():
    if k not in properties:  # pragma: no cover
      api.assertions.fail('Property {} not present, expected {!r}'.format(k, v))
    else:
      api.assertions.assertEqual(properties[k], v)


def GenTests(api: TEST_DEPS):
  src_revision = api.bot_update.gen_revision('src')
  yield api.test(
    'overridden-dep',
    api.chromium_tests_builder_config.ci_build(
      builder_group='fake-group',
      builder='fake-builder',
      revision=src_revision,
      builder_db=ctbc.BuilderDatabase.create(
        {
          'fake-group': {
            'fake-builder': ctbc.BuilderSpec.create(
              chromium_config='chromium',
              gclient_config='chromium',
              gclient_apply_config=['override_foo'],
            ),
            'fake-tester': ctbc.BuilderSpec.create(
              execution_mode=ctbc.TEST,
              chromium_config='chromium',
              gclient_config='chromium',
              gclient_apply_config=['override_foo'],
              parent_buildername='fake-builder',
            ),
          },
        }
      ),
    ),
    api.properties(
      expected_trigger_properties={
        'deps_revision_overrides': {
          'src': src_revision,
          'src/foo': api.bot_update.gen_revision('src/foo'),
        },
      }
    ),
    api.post_process(post_process.DropExpectation),
  )
