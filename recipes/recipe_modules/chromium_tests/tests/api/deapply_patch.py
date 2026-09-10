# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import collections

from recipe_engine.post_process import DropExpectation, StepSuccess

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
  chromium,
  chromium_tests,
  chromium_tests_builder_config,
)
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  platform,
  properties,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  chromium: chromium.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  platform: platform.API
  properties: properties.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  platform: platform.TEST_API
  properties: properties.TEST_API


def RunSteps(api: DEPS):
  # Create a nested step so that setup steps can be easily filtered out
  with api.step.nest('setup steps'):
    _, builder_config = api.chromium_tests_builder_config.lookup_builder()
    api.chromium_tests.configure_build(builder_config)
    update_step, build_dir, _ = api.chromium_tests.prepare_checkout(
      builder_config
    )
  api.chromium_tests.deapply_patch(update_step, build_dir)


def GenTests(api: TEST_DEPS):

  ctbc_api = api.chromium_tests_builder_config

  yield api.test(
    'basic',
    api.platform('win', 64),
    api.chromium.try_build(
      builder_group='fake-try-group',
      builder='fake-try-builder',
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_try_builder()
      .with_mirrored_builder(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.post_process(StepSuccess, 'bot_update (without patch)'),
    api.post_process(StepSuccess, 'gclient runhooks (without patch)'),
    api.post_process(DropExpectation),
  )
