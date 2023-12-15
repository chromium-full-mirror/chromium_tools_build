# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import DropExpectation, StepCommandContains

DEPS = [
    'recipe_engine/path',
    'chromium',
    'chromium_android',
]


def RunSteps(api):
  api.chromium.set_config('chromium')
  api.chromium_android.set_config('non_device_wipe_provisioning')
  api.path['checkout'] = api.path['cache'] / 'builder' / 'src'

  api.chromium_android.provision_devices(emulators=True)


def GenTests(api):
  yield api.test(
      'basic',
      api.post_process(StepCommandContains, 'provision_devices', [
          'vpython3',
          '[CACHE]/builder/src/build/android/provision_devices.py',
      ]),
      api.post_process(StepCommandContains, 'provision_devices', [
          '--emulators',
      ]),
      api.post_process(DropExpectation),
  )
