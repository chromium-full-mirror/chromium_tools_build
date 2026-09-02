# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import json

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium_swarming
from RECIPE_MODULES.recipe_engine import (
    assertions,
    platform,
    properties,
    step,
)


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  chromium_swarming: chromium_swarming.API
  platform: platform.API
  properties: properties.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  properties: properties.TEST_API

from recipe_engine import post_process
from recipe_engine.recipe_api import Property

PROPERTIES = {
    'task_to_retry': Property(default=None, kind=dict),
    'none_trigger_output': Property(default=False, kind=bool),
    'expected_value': Property(default=None, kind=str),
    'expected_inv_names': Property(default=None, kind=list),
}


def RunSteps(api: DEPS, task_to_retry, none_trigger_output, expected_value,
             expected_inv_names):
  kwargs = {}
  if task_to_retry:
    class FakeTask:
      def __init__(self):
        self.trigger_output = task_to_retry
    kwargs['task_to_retry'] = FakeTask()
  task = api.chromium_swarming.task(
      name='test-task', cas_input_root='00deadbeef00/size', **kwargs)
  task.raw_trigger_output = None if none_trigger_output else {
      'tasks': {
          '0': {
              'shard_index': 0,
              'task_id': '10',
              'invocation': 'invocations/10',
          },
          '1': {
              'shard_index': 1,
              'task_id': '11',
              'invocation': 'invocations/11',
          },
          '2': {
              'shard_index': 2,
              'task_id': '12',
              'invocation': 'invocations/12',
          },
      },
  }
  if not none_trigger_output:
    api.assertions.assertEqual(
        ' '.join(t['task_id'] for t in task.trigger_output['tasks'].values()),
        expected_value)
  else:
    # Ensure this property doesn't throw an exception
    _ = task.trigger_output

  if expected_inv_names:
    api.assertions.assertEqual(task.get_invocation_names(), expected_inv_names)


def GenTests(api: TEST_DEPS):
  yield api.test(
      'basic',
      api.properties(expected_value='10 11 12'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'task_to_retry',
      api.properties(
          task_to_retry={
              'tasks': {
                  0: {
                      'task_id': '90',
                      'shard_index': 0,
                  },
                  1: {
                      'task_id': '91',
                      'shard_index': 1,
                  },
                  2: {
                      'task_id': '92',
                      'shard_index': 2,
                  },
                  3: {
                      'task_id': '93',
                      'shard_index': 3,
                  },
                  4: {
                      'task_id': '94',
                      'shard_index': 4,
                  },
              },
          },
          expected_value='10 11 12 93 94'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'none_trigger_output',
      api.properties(none_trigger_output=True,),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'get_invocation_names',
      api.properties(
          expected_value='10 11 12',
          expected_inv_names=[
              'invocations/10', 'invocations/11', 'invocations/12'
          ]),
      api.post_process(post_process.DropExpectation),
  )
