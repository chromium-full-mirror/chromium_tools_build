# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine.post_process import (DropExpectation, StepCommandContains,
                                        StepSuccess)

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
  source_dir = api.path.cache_dir / 'builder/src'
  build_dir = api.chromium.default_build_dir(source_dir)
  api.chromium_android.upload_build(source_dir, build_dir, 'test-bucket',
                                    'test/path')


def GenTests(api: RecipeTestApi):
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
