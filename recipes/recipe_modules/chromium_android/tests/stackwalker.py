# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine.post_process import DropExpectation, StepSuccess

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


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API
  path: path.TEST_API


def RunSteps(api: DEPS):
  source_dir = api.path.start_dir / 'checkout'
  api.chromium.set_config('chromium')
  build_dir = api.chromium.default_build_dir(source_dir)

  api.chromium_android.stackwalker(source_dir,
                                   [build_dir / 'lib.unstripped/libchrome.so'])


def GenTests(api: TEST_DEPS):
  yield api.test(
      'basic',
      api.chromium.ci_build(),
      api.path.exists(
          api.path.start_dir /
          'checkout/out/2796-Linux_Builder/lib.unstripped/libchrome.so',
          api.path.start_dir /
          'checkout/out/2796-Linux_Builder/microdump_stackwalk',
          api.path.start_dir / 'checkout/out/2796-Linux_Builder/dump_syms',
      ),
      api.post_process(StepSuccess,
                       'generate breakpad symbols for libchrome.so'),
      api.post_process(StepSuccess, 'symbolized breakpad crashes'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'missing_binaries',
      api.chromium.ci_build(),
      api.post_process(StepSuccess, 'skipping stackwalker step'),
      api.post_process(DropExpectation),
  )
