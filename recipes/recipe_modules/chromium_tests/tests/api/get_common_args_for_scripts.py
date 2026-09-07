# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine.post_process import DropExpectation, StepCommandContains

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium, chromium_tests, chromium_tests_builder_config
from RECIPE_MODULES.recipe_engine import path, step


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  path: path.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API


def RunSteps(api: DEPS):
  source_dir = api.path.cache_dir / 'builder/src'
  build_dir = source_dir / 'out' / 'some-build-dir'

  _, builder_config = api.chromium_tests_builder_config.lookup_builder()
  api.chromium_tests.configure_build(builder_config)
  common_args, _, _ = api.chromium_tests.get_common_args_for_scripts(
      source_dir, build_dir)
  api.step(
      'sample script',
      [
          'python3',
          source_dir / 'testing/scripts/example.py',
      ] + common_args,
  )


def GenTests(api: TEST_DEPS):
  yield api.test(
      'basic',
      api.chromium.ci_build(
          builder_group='chromium.perf',
          builder='linux-r350-perf',
      ),
      api.post_process(StepCommandContains, 'sample script', [
          '--build-dir',
          '[CACHE]/builder/src/out/some-build-dir',
          '--paths',
          '{"checkout": "[CACHE]/builder/src"}',
          '--properties',
          ('{"bot_id": "fake-bot-id", '
           '"buildername": "linux-r350-perf", '
           '"buildnumber": 571, '
           '"mastername": "chromium.perf", '
           '"slavename": "fake-bot-id", '
           '"target_platform": "linux"}'),
      ]),
      api.post_process(DropExpectation),
  )
