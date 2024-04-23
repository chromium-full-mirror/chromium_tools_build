# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

DEPS = [
  'adb',
  'recipe_engine/path',
  'recipe_engine/step',
]


def RunSteps(api):
  api.path.checkout_dir = api.path.cache_dir / 'builder' / 'src'

  default_adb_path = api.adb.adb_path()
  api.adb.set_adb_path(api.path.checkout_dir.joinpath('custom', 'adb', 'path'))
  custom_adb_path = api.adb.adb_path()

  api.step('adb paths', [])
  api.step.active_result.presentation.logs['result'] = [
    'default: %r' % (default_adb_path,),
    'custom: %r' % (custom_adb_path,),
  ]

  api.adb.root_devices()


def GenTests(api):
  yield api.test('basic')
