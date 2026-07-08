# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

DEPS = [
    'dawn',
    'depot_tools/gclient',
    'recipe_engine/properties',
]

TEST_CONFIGS = [
    'dawn',
]


def RunSteps(api):
  for config_name in TEST_CONFIGS:
    api.gclient.make_config(config_name)

  api.gclient.set_config('dawn')
  api.gclient.apply_config(api.properties.get('apply_gclient_config'))


def GenTests(api):
  yield api.test(
      'dawn_node',
      api.properties(apply_gclient_config='dawn_node'),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'dawn_wasm',
      api.properties(apply_gclient_config='dawn_wasm'),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'checkout_litert_lm',
      api.properties(apply_gclient_config='checkout_litert_lm'),
      api.post_process(post_process.DropExpectation),
  )
