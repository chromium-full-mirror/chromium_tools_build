# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import (DoesNotRun, DropExpectation,
                                        StepCommandContains, StepSuccess)

DEPS = [
    'chromium_android',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/properties',
    'recipe_engine/path',
    'recipe_engine/step',
]


def RunSteps(api):
  source_dir = api.path.cache_dir / 'builder/src'
  if api.properties['denylist_exists']:
    api.path.mock_add_paths(api.chromium_android.denylist_file(source_dir))
  api.chromium_android._devices = ['serial1', 'serial2']
  available_devices = api.chromium_android.non_denylisted_devices(source_dir)
  api.step('print devices', ['echo'] + available_devices)


def GenTests(api):
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
