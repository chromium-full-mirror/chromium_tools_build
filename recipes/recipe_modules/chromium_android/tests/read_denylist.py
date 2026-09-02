# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine.post_process import (DoesNotRun, DropExpectation,
                                        StepCommandContains, StepSuccess)

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium_android
from RECIPE_MODULES.recipe_engine import (
    file,
    json,
    path,
    properties,
    step,
)


@dataclass
class DEPS(RecipeScriptApi):
  chromium_android: chromium_android.API
  file: file.API
  json: json.API
  path: path.API
  properties: properties.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  file: file.TEST_API
  properties: properties.TEST_API


def RunSteps(api: DEPS):
  source_dir = api.path.cache_dir / 'builder/src'
  if api.properties['denylist_exists']:
    api.path.mock_add_paths(api.chromium_android.denylist_file(source_dir))
  api.chromium_android._devices = ['serial1', 'serial2']
  available_devices = api.chromium_android.non_denylisted_devices(source_dir)
  api.step('print devices', ['echo'] + available_devices)


def GenTests(api: TEST_DEPS):
  yield api.test(
      'denylisted_device',
      api.properties(denylist_exists=True),
      api.override_step_data('read_denylist_file',
                             api.file.read_json({'serial1': {}})),
      api.post_process(StepSuccess, 'read_denylist_file'),
      api.post_process(StepCommandContains, 'print devices', [
          'echo',
          'serial2',
      ]),
      api.post_process(DropExpectation),
  )
  yield api.test(
      'no_denylist',
      api.properties(denylist_exists=False),
      api.post_process(DoesNotRun, 'read_denylist_file'),
      api.post_process(StepCommandContains, 'print devices', [
          'echo',
          'serial1',
          'serial2',
      ]),
      api.post_process(DropExpectation),
  )
