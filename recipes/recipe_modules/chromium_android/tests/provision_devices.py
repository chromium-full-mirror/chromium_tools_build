# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine.post_process import DropExpectation, StepCommandContains

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium, chromium_android
from RECIPE_MODULES.recipe_engine import path


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_android: chromium_android.API
  path: path.API


def RunSteps(api: DEPS):
  api.chromium.set_config('chromium')
  api.chromium_android.set_config('base_config')
  source_dir = api.path.cache_dir / 'builder/src'

  api.chromium_android.provision_devices(source_dir, emulators=True)


def GenTests(api: RecipeTestApi):
  yield api.test(
    'basic',
    api.post_process(
      StepCommandContains,
      'provision_devices',
      [
        'vpython3',
        '[CACHE]/builder/src/build/android/provision_devices.py',
      ],
    ),
    api.post_process(
      StepCommandContains,
      'provision_devices',
      [
        '--emulators',
      ],
    ),
    api.post_process(DropExpectation),
  )
