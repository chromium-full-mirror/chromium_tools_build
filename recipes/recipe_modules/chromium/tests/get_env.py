# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import (DropExpectation, StatusSuccess,
                                        StepEnvContains)

DEPS = [
    'chromium',
    'recipe_engine/context',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/step',
]


def RunSteps(api):
  api.chromium.set_config('chromium', TARGET_PLATFORM='mac')
  for config in api.properties.get('chromium_apply_config', []):
    api.chromium.apply_config(config)

  with api.context(env=api.chromium.get_env()):
    api.step('test', ['echo', 'foo'])


def GenTests(api):

  yield api.test(
      'basic',
      api.platform('mac', 64),
      api.post_process(StatusSuccess),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'goma_store_only',
      api.platform('mac', 64),
      api.properties(chromium_apply_config=['goma_store_only']),
      api.post_process(StepEnvContains, 'test', {'GOMA_STORE_ONLY': 'True'}),
      api.post_process(StatusSuccess),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'goma_large_cache_file',
      api.platform('mac', 64),
      api.properties(chromium_apply_config=['goma_large_cache_file']),
      api.post_process(StepEnvContains, 'test',
                       {'GOMA_DEPS_CACHE_MAX_PROTO_SIZE_IN_MB': '256'}),
      api.post_process(StatusSuccess),
      api.post_process(DropExpectation),
  )
