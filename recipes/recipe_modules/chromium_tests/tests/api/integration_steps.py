# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
  chromium,
  chromium_swarming,
  chromium_tests,
  chromium_tests_builder_config,
  test_utils,
)
from RECIPE_MODULES.recipe_engine import buildbucket, platform, properties


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  chromium: chromium.API
  chromium_swarming: chromium_swarming.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  platform: platform.API
  properties: properties.API
  test_utils: test_utils.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API
  chromium_tests: chromium_tests.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  platform: platform.TEST_API
  properties: properties.TEST_API


def RunSteps(api: DEPS):
  api.chromium_swarming.set_default_dimension('os', 'Linux')
  builder_id, builder_config = (
    api.chromium_tests_builder_config.lookup_builder()
  )
  with api.chromium.chromium_layout():
    return api.chromium_tests.integration_steps(builder_id, builder_config)


def GenTests(api: TEST_DEPS):
  ctbc_api = api.chromium_tests_builder_config

  yield api.test(
    'deapply_deps_after_failure',
    api.platform('linux', 64),
    api.chromium.try_build(
      builder_group='fake-group',
      builder='fake-builder',
      git_repo='https://chromium.googlesource.com/v8/v8.git',
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_try_builder()
      .with_mirrored_builder(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.properties(
      swarm_hashes={
        'blink_web_tests': 'dummy hash for blink_web_tests/size',
      }
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-builder': {
          'isolated_scripts': [
            {
              'test': 'blink_web_tests',
              'name': 'blink_web_tests',
              'swarming': {},
              'results_handler': 'layout tests',
            },
          ],
        },
      },
    ),
    api.chromium_tests.gen_swarming_and_rdb_results(
      'blink_web_tests', 'with patch', failures=['Test.One']
    ),
    api.chromium_tests.gen_swarming_and_rdb_results(
      'blink_web_tests', 'retry shards with patch', failures=['Test.One']
    ),
    api.post_process(post_process.MustRun, 'blink_web_tests (with patch)'),
    api.post_process(
      post_process.MustRun, 'blink_web_tests (retry shards with patch)'
    ),
    api.post_process(post_process.MustRun, 'blink_web_tests (without patch)'),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )
