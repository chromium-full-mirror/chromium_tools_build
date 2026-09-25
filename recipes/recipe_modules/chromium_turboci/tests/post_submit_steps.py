# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
  chromium,
  chromium_tests,
  chromium_tests_builder_config,
  chromium_turboci,
)
from RECIPE_MODULES.recipe_engine import platform, runtime


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  chromium_turboci: chromium_turboci.API
  platform: platform.API
  runtime: runtime.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  platform: platform.TEST_API
  runtime: runtime.TEST_API


def RunSteps(api: DEPS):
  builder_id, builder_config = (
    api.chromium_tests_builder_config.lookup_builder()
  )
  with (
    api.chromium.chromium_layout(),
    api.chromium_turboci.display_turboci_checks(),
  ):
    build_result, _ = api.chromium_tests.main_waterfall_steps(
      builder_id, builder_config
    )
    return build_result


def GenTests(api: TEST_DEPS):
  ctbc_api = api.chromium_tests_builder_config

  def builder_props():
    return api.chromium.ci_build(
      builder_group='fake-group',
      builder='fake-builder',
      experiments=['luci.buildbucket.run_in_turboci'],
    ) + ctbc_api.properties(
      ctbc_api.properties_assembler_for_ci_builder(
        builder_group='fake-group',
        builder='fake-builder',
      ).assemble()
    )

  yield api.test(
    'basic',
    api.platform('linux', 64),
    builder_props(),
    api.post_process(post_process.StepSuccess, 'TurboCI Checks'),
    api.post_process(post_process.StatusSuccess),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'no_show_without_flag',
    api.platform('linux', 64),
    api.chromium.ci_build(
      builder_group='fake-group',
      builder='fake-builder',
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_ci_builder(
        builder_group='fake-group',
        builder='fake-builder',
      ).assemble()
    ),
    api.post_process(post_process.DoesNotRun, 'TurboCI Checks'),
    api.post_process(post_process.StatusSuccess),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'no_show_in_global_shutdown',
    api.platform('linux', 64),
    builder_props(),
    api.runtime.global_shutdown_on_step('bot_update'),
    api.post_process(post_process.DoesNotRun, 'TurboCI Checks'),
    api.expect_status('CANCELED'),
    api.post_process(post_process.DropExpectation),
  )
