# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


DEPS = [
    'adb',
    'recipe_engine/path',
    'recipe_engine/step',
    'recipe_engine/assertions',
]


def RunSteps(api):
  source_dir = api.path.cache_dir / 'builder/src'
  default_adb_path = api.adb.default_adb_path(source_dir)
  api.assertions.assertEqual(
      default_adb_path,
      source_dir / 'third_party/android_sdk/public/platform-tools/adb')

  api.adb.root_devices(source_dir / 'custom/adb/path')


def GenTests(api):
  yield api.test('basic')
