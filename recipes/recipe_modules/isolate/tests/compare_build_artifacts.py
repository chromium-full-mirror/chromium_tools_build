# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium, isolate
from RECIPE_MODULES.recipe_engine import path, platform, properties


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  isolate: isolate.API
  path: path.API
  platform: platform.API
  properties: properties.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  platform: platform.TEST_API
  properties: properties.TEST_API


def RunSteps(api: DEPS):
  api.chromium.set_config('chromium')
  source_dir = api.path.cache_dir / 'builder/src'

  api.isolate.compare_build_artifacts(source_dir, 'first_dir', 'second_dir')


def GenTests(api: TEST_DEPS):
  yield api.test(
    'basic',
    api.properties(buildername='test_buildername', buildnumber=123),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'failure',
    api.properties(buildername='test_buildername', buildnumber=123),
    api.step_data('compare_build_artifacts', retcode=1),
    api.post_process(
      post_process.SummaryMarkdown,
      "Step('compare_build_artifacts') (retcode: 1)",
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'win',
    api.platform.name('win'),
    api.properties(buildername='test_buildername', buildnumber=123),
    api.post_check(
      post_process.StepCommandContains,
      'gsutil upload',
      (
        'gs://chrome-determinism/test_buildername/123/'
        'deterministic_build_diffs.tgz'
      ),
    ),
    api.post_process(post_process.DropExpectation),
  )
