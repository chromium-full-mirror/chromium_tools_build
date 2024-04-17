# Copyright (c) 2014 ThE Chromium Authors. All Rights Reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import recipe_api

class AdbApi(recipe_api.RecipeApi):
  def __init__(self, **kwargs):
    super().__init__(**kwargs)
    self._custom_adb_path = None
    self._devices = None

  def __call__(self, cmd, serial=None, **kwargs):  # pragma: nocover
    """Run an ADB command."""
    cmd_prefix = [self.adb_path()]
    if serial:
      cmd_prefix.extend(['-s', serial])
    return self.m.step(cmd=cmd_prefix + cmd, **kwargs)

  def set_adb_path(self, adb_path):
    self._custom_adb_path = adb_path

  def adb_path(self):
    if self._custom_adb_path:
      return self._custom_adb_path
    return self.m.path.checkout_dir.join('third_party', 'android_sdk', 'public',
                                         'platform-tools', 'adb')

  def list_devices(self, step_test_data=None, **kwargs):
    cmd = [
        'python3',
        self.resource('list_devices.py'),
        repr([
            str(self.adb_path()),
            'devices',
        ]),
        self.m.json.output(),
    ]

    result = self.m.step(
        'List adb devices',
        cmd,
        step_test_data=step_test_data or self.test_api.device_list,
        **kwargs)

    self._devices = result.json.output

  @property
  def devices(self):
    assert self._devices is not None, (
        "devices is only available after yielding list_devices()")
    return self._devices

  def root_devices(self, **kwargs):
    self.list_devices(**kwargs)
    cmd = ([
        'python3',
        self.resource('root_devices.py'),
        self.adb_path(),
    ] + self.devices)
    self.m.step('Root devices', cmd, **kwargs)
