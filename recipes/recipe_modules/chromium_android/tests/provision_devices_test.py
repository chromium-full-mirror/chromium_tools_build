# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine.post_process import (DropExpectation, StepException,
                                        StepWarning, SummaryMarkdown)

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
  source_dir = api.path.cache_dir / 'builder/src'
  api.chromium.set_config(
      'base_config',
      BUILD_CONFIG='Debug',
      TARGET_ARCH='arm',
      TARGET_BITS=32,
      TARGET_PLATFORM='android')
  api.chromium_android.set_config('base_config')
  api.chromium_android.provision_devices(source_dir)


def GenTests(api: RecipeTestApi):
  yield api.test(
      'warning_exit_code',
      api.step_data('provision_devices', retcode=88),
      api.post_process(StepWarning, 'provision_devices'),
      api.post_process(SummaryMarkdown,
                       "Warning: Step('provision_devices') (retcode: 88)"),
      api.expect_status('FAILURE'),
      api.post_process(DropExpectation),
  )
  yield api.test(
      'infra_failure_exit_code',
      api.step_data('provision_devices', retcode=87),
      api.post_process(StepException, 'provision_devices'),
      api.post_process(
          SummaryMarkdown,
          "Infra Failure: Step('provision_devices') (retcode: 87)"),
      api.expect_status('INFRA_FAILURE'),
      api.post_process(DropExpectation),
  )
