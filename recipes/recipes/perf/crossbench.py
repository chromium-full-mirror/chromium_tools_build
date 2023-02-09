# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""
Recipe for running CBB tests in Crossbench
"""

DEPS = [
    'depot_tools/bot_update',
    'depot_tools/gclient',
    'recipe_engine/step',
]


def RunSteps(api):
  api.gclient.set_config('crossbench')
  api.bot_update.ensure_checkout()
  api.gclient.runhooks()

  api.step('Run CBB Tests', ['vpython3', 'crossbench/tests/cbb/cbb_runner.py'])


def GenTests(api):
  yield api.test('basic')
