# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from RECIPE_MODULES.build import chromium_types
from RECIPE_MODULES.build.chromium_utr.instruction import (
    get_utr_instruction, get_utr_compile_instruction)

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import repro_instructions
from RECIPE_MODULES.recipe_engine import (
    buildbucket,
    led,
    properties,
    step,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  led: led.API
  properties: properties.API
  repro_instructions: repro_instructions.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  properties: properties.TEST_API


def RunSteps(api: DEPS):
  get_utr_instruction('run', 'project', 'bucket', 'builder', ['test 1'],
                      ['filter'])
  get_utr_instruction('run', 'project', 'bucket', 'builder', [], ['filter'])

  step_result = api.step.empty('compile step')
  builder_id = chromium_types.BuilderId.create_for_group(
      'builder group', 'builder name')
  get_utr_compile_instruction(api, step_result, builder_id)


def GenTests(api: TEST_DEPS):
  yield api.test('basic', api.post_process(post_process.DropExpectation))
  yield api.test(
      'orchestrator',
      api.properties(orchestrator={'builder_name': 'fake-orch-builder'}),
      api.post_process(post_process.DropExpectation),
  )
