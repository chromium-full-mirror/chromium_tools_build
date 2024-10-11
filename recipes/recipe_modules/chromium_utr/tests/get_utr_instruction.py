# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from google.protobuf import timestamp_pb2

from recipe_engine import post_process
from recipe_engine.recipe_api import Property

from RECIPE_MODULES.build import chromium
from RECIPE_MODULES.build.chromium_utr.instruction import (
    get_utr_instruction, get_utr_compile_instruction)

DEPS = [
    'recipe_engine/buildbucket',
    'recipe_engine/led',
    'recipe_engine/properties',
    'recipe_engine/step',
    'repro_instructions',
]


def RunSteps(api):
  get_utr_instruction('run', 'project', 'bucket', 'builder', ['test 1'],
                      ['filter'])
  get_utr_instruction('run', 'project', 'bucket', 'builder', [], ['filter'])

  step_result = api.step.empty('compile step')
  builder_id = chromium.BuilderId.create_for_group('builder group',
                                                   'builder name')
  get_utr_compile_instruction(api, step_result, builder_id)


def GenTests(api):
  yield api.test('basic', api.post_process(post_process.DropExpectation))
