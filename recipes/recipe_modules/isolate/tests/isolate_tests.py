# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine.post_process import (DropExpectation, MustRun,
                                        PropertyEquals, StepCommandContains)

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import isolate, siso
from RECIPE_MODULES.recipe_engine import buildbucket, path, properties


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  isolate: isolate.API
  path: path.API
  properties: properties.API
  siso: siso.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  properties: properties.TEST_API


def RunSteps(api: DEPS):
  source_dir = api.path.cache_dir / 'builder/src'
  api.isolate.isolate_tests(
      source_dir / 'out/Release',
      targets=['dummy_target_1', 'dummy_target_2'],
      source_dir=source_dir,
      use_siso_isolate=api.properties.get('use_siso_isolate'),
  )


def GenTests(api: TEST_DEPS):
  yield api.test(
      'basic',
      api.post_process(StepCommandContains, 'isolate tests', [
          '[CACHE]/builder/src/out/Release/dummy_target_1.isolated.gen.json',
          '[CACHE]/builder/src/out/Release/dummy_target_2.isolated.gen.json',
      ]),
      api.post_process(
          PropertyEquals, 'swarm_hashes', {
              'dummy_target_1': '[dummy hash for dummy_target_1/dummy size]',
              'dummy_target_2': '[dummy hash for dummy_target_2/dummy size]'
          }),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'siso_isolate_test',
      api.properties(use_siso_isolate=True),
      api.post_process(MustRun, 'isolate tests'),
      api.post_process(StepCommandContains, 'isolate tests', [
          'dummy_target_1',
          'dummy_target_2',
      ]),
      api.post_process(
          PropertyEquals, 'swarm_hashes', {
              'dummy_target_1': '[dummy hash for dummy_target_1/dummy size]',
              'dummy_target_2': '[dummy hash for dummy_target_2/dummy size]'
          }),
      api.post_process(DropExpectation),
  )
