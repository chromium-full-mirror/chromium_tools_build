# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

from PB.recipe_modules.build.chromium_compilator.properties import InputProperties

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
    chromium,
    chromium_compilator,
    chromium_tests_builder_config,
    chromium_turboci,
)
from RECIPE_MODULES.recipe_engine import properties


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_compilator: chromium_compilator.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  chromium_turboci: chromium_turboci.API
  properties: properties.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  properties: properties.TEST_API

PROPERTIES = InputProperties


def RunSteps(api: DEPS, properties):
  with api.chromium.chromium_layout(), \
       api.chromium_turboci.display_turboci_checks():
    return api.chromium_compilator.compilator_steps(properties)


def GenTests(api: TEST_DEPS):
  ctbc_api = api.chromium_tests_builder_config

  yield api.test(
      'basic',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-compilator',
      ),
      api.properties(
          InputProperties(
              orchestrator=InputProperties.Orchestrator(
                  builder_name='fake-orchestrator',
                  builder_group='fake-try-group'))),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).with_mirrored_tester(
              builder_group='fake-group',
              builder='fake-tester',
          ).assemble()),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )
