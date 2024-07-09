# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import (DropExpectation, StepCommandContains,
                                        StepSuccess)

DEPS = [
    'recipe_engine/path',
    'build',
    'chromium',
    'chromium_android',
]


def RunSteps(api):
  api.chromium.set_config('chromium')
  source_dir = api.path.cache_dir / 'builder/src'
  api.chromium_android.upload_build(source_dir, 'test-bucket', 'test/path')


def GenTests(api):
  yield api.test(
      'basic',
      api.post_process(StepSuccess, 'zip_build_product'),
      api.post_process(StepSuccess, 'gsutil upload_build_product'),
      api.post_process(StepCommandContains, 'gsutil upload_build_product', [
          'cp',
          '[CACHE]/builder/src/out/build_product.zip',
          'gs://test-bucket/test/path',
      ]),
      api.post_process(DropExpectation),
  )
