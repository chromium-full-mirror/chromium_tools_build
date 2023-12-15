# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import re

from recipe_engine import post_process

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc
from RECIPE_MODULES.build.chromium_tests import steps

DEPS = [
    'chromium',
    'chromium_tests',
    'chromium_tests_builder_config',
    'code_coverage',
    'profiles',
    'test_utils',
    'recipe_engine/path',
]


def RunSteps(api):
  _, builder_config = (
      api.chromium_tests_builder_config.lookup_builder(use_try_db=True))
  api.chromium_tests.configure_build(builder_config)
  # Fake path.
  api.profiles.src_dir = api.path['start_dir']
  api.code_coverage.src_dir = api.path['start_dir']
  api.path['checkout'] = api.path['start_dir']

  api.path.mock_add_paths(
      api.profiles.profile_dir().join('unit-merged.profdata'))
  api.path.mock_add_paths(
      api.profiles.profile_dir().join('overall-merged.profdata'))

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


def GenTests(api):
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
