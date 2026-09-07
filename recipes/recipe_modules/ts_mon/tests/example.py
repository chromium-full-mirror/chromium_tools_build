# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine.post_process import DropExpectation, StepCommandContains

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import ts_mon


@dataclass
class DEPS(RecipeScriptApi):
  ts_mon: ts_mon.API


def RunSteps(api: DEPS):
  api.ts_mon.send_value('/example/metric', 'counter', 42)
  api.ts_mon.send_value(
      name='/example/metric',
      metric_type='float',
      value=42.0,
      fields={'foo': 'bar'},
      service_name='example_service',
      job_name='example_job',
      step_name='custom upload step name')
  api.ts_mon.send_values_batch(
      '/example/metric', 'counter', [(42, {'a': 1}), (43, {'a': 2})])


def GenTests(api: RecipeTestApi):
  yield api.test(
      'basic',
      api.post_process(StepCommandContains, 'upload ts_mon metrics', [
          '--counter-file',
          '{"name": "/example/metric", "value": 42}',
      ]),
      api.post_process(StepCommandContains, 'custom upload step name', [
          '--ts-mon-task-service-name',
          'example_service',
          '--ts-mon-task-job-name',
          'example_job',
          '--float-file',
          '{"foo": "bar", "name": "/example/metric", "value": 42.0}',
      ]),
      api.post_process(StepCommandContains, 'upload ts_mon metrics (2)', [
          '--counter-file',
          '{"a": 1, "name": "/example/metric", "value": 42}\n'
          '{"a": 2, "name": "/example/metric", "value": 43}',
      ]),
      api.post_process(DropExpectation),
  )
