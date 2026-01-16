# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

DEPS = [
    'angle_v2',
    'depot_tools/gclient',
    'recipe_engine/properties',
]

TEST_CONFIGS = [
    'angle_v2',
]


def RunSteps(api):
  for config_name in TEST_CONFIGS:
    api.gclient.make_config(config_name)

  api.gclient.set_config('angle_v2')
  api.gclient.apply_config(api.properties.get('apply_gclient_config'))


def GenTests(api):
  yield api.test(
      'angle_v2_android',
      api.properties(apply_gclient_config='angle_v2_android'),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'angle_v2_nointernal',
      api.properties(apply_gclient_config='angle_v2_nointernal'),
      api.post_process(post_process.DropExpectation),
  )