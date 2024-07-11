# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import (
    DropExpectation,
    StepCommandContains,
)

DEPS = [
    'v8_auto_roller',
]


def RunSteps(api):
  api.v8_auto_roller.setup_target(
      'v8',
      'https://chromium.googlesource.com/v8/v8',
      requires_chromium_checkout=True,
  )


def GenTests(api):
  yield api.test(
      'default',
      api.post_process(
          StepCommandContains,
          'Setup.bot_update',
          "cache_dir = '[CACHE]/git'\nsolutions = [{'deps_file': '.DEPS.git', 'managed': True, 'name': 'v8', 'url': 'https://chromium.googlesource.com/v8/v8'}, {'custom_vars': {'checkout_telemetry_dependencies': 'False'}, 'deps_file': '.DEPS.git', 'managed': True, 'name': 'src', 'url': 'https://chromium.googlesource.com/chromium/src.git'}]\ntarget_os = ['android', 'win']",
      ),
      api.post_process(DropExpectation),
  )
