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
  'expected_value': Property(default=None, kind=str),
}


def RunSteps(api: DEPS, task_to_retry, expected_value):
  kwargs = {}
  if task_to_retry:

    class FakeTask:
      def __init__(self):
        self.trigger_output = task_to_retry

    kwargs['task_to_retry'] = FakeTask()
  task = api.chromium_swarming.task(
    name='test-task', cas_input_root='00deadbeef00/size', **kwargs
  )
  task.raw_trigger_output = {
    'tasks': {
      0: {
        'shard_index': 0,
        'task_id': '10',
      },
      1: {
        'shard_index': 1,
        'task_id': '11',
      },
      2: {
        'shard_index': 2,
        'task_id': '12',
      },
    },
  }
  api.assertions.assertEqual(
    ' '.join(t['task_id'] for t in task.trigger_output['tasks'].values()),
    expected_value,
  )


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
            'shard_index': 0,
            'task_id': '90',
          },
          1: {
            'shard_index': 1,
            'task_id': '91',
          },
          2: {
            'shard_index': 2,
            'task_id': '92',
          },
          3: {
            'shard_index': 3,
            'task_id': '93',
          },
          4: {
            'shard_index': 4,
            'task_id': '94',
          },
        },
      },
      expected_value='10 11 12 93 94',
    ),
    api.post_process(post_process.DropExpectation),
  )
