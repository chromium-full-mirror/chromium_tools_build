# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import re

from recipe_engine import post_process

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc
from RECIPE_MODULES.build.chromium_tests import steps

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
    chromium,
    chromium_tests,
    chromium_tests_builder_config,
    code_coverage,
    profiles,
    test_utils,
)
from RECIPE_MODULES.recipe_engine import path


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  code_coverage: code_coverage.API
  path: path.API
  profiles: profiles.API
  test_utils: test_utils.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  code_coverage: code_coverage.TEST_API


def RunSteps(api: DEPS):
  _, builder_config = (
      api.chromium_tests_builder_config.lookup_builder(use_try_db=True))
  api.chromium_tests.configure_build(builder_config)

  # Fake paths.
  source_dir = api.path.start_dir
  build_dir = api.chromium.default_build_dir(source_dir)
  api.profiles.source_dir = source_dir
  api.code_coverage.source_dir = source_dir
  api.code_coverage.build_dir = build_dir

  api.path.mock_add_paths(
      api.profiles.profile_dir().joinpath('unit-merged.profdata'))
  api.path.mock_add_paths(
      api.profiles.profile_dir().joinpath('overall-merged.profdata'))

  test_specs = [
      steps.SwarmingGTestTestSpec.create('base_unittests'),
  ]
  tests = [s.get_test(api.chromium_tests) for s in test_specs]

  for test in tests:
    step = test.name
    api.profiles.profile_dir(step)
    api.code_coverage.shard_merge(
        step,
        test.target_name,
        additional_merge=getattr(test.spec, 'merge', None),
        skip_validation=True,
        sparse=True,
    )

  api.code_coverage.process_coverage_data(
      tests, override_builder_name='fake-builder')


def GenTests(api: TEST_DEPS):
  ctbc_api = api.chromium_tests_builder_config

  yield api.test(
      'basic',
      api.code_coverage(use_clang_coverage=True),
      api.chromium.generic_build(
          project='reviver-project',
          bucket='reviver-bucket',
          builder='fake-runner',
          builder_group='fake-group',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder='fake-runner',
              builder_group='fake-group',
          ).assemble()),
      api.code_coverage(use_clang_coverage=True),
      api.post_process(post_process.MustRunRE, '.*coverage data.*'),
      api.post_process(
          post_process.StepCommandContains,
          'gsutil Upload coverage artifacts',
          [re.compile('.*/reviver-bucket/fake-builder/.*')]),
      api.post_process(post_process.DropExpectation),
  )
