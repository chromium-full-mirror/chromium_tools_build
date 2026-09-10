# Copyright 2014 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import recipe_api
from recipe_engine.config_types import Path


class AdbApi(recipe_api.RecipeApi):
  @staticmethod
  def default_adb_path(source_dir: Path):
    return source_dir / 'third_party/android_sdk/public/platform-tools/adb'

  def list_devices(self, adb_path: Path, *, step_test_data=None, **kwargs):
    cmd = [
      'python3',
      self.resource('list_devices.py'),
      repr([str(adb_path), 'devices']),
      self.m.json.output(),
    ]

    result = self.m.step(
      'List adb devices',
      cmd,
      step_test_data=step_test_data or self.test_api.device_list,
      **kwargs,
    )

    return result.json.output

  def root_devices(self, adb_path: Path, **kwargs):
    devices = self.list_devices(adb_path, **kwargs)
    cmd = [
      'python3',
      self.resource('root_devices.py'),
      adb_path,
      *devices,
    ]
    self.m.step('Root devices', cmd, **kwargs)
    return devices
