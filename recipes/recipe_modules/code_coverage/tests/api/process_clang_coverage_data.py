# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Test |process_clang_coverage_data| API when |upload_metadata|=True.

The `|upload_metadata|=True` branch is not covered in full.py where
|process_coverage_data| is invoked.
"""

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
  api.profiles.src_dir = api.path.start_dir
  api.code_coverage.src_dir = api.path.start_dir
  api.path.checkout_dir = api.path.start_dir

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

  api.code_coverage.process_clang_coverage_data(tests, upload_metadata=True)


def GenTests(api):
  ctbc_api = api.chromium_tests_builder_config

  yield api.test(
      'basic',
      api.code_coverage(use_clang_coverage=True),
      api.chromium.generic_build(
          project='fake-project',
          bucket='fake-bucket',
          builder='fake-builder',
          builder_group='fake-group',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder='fake-builder',
              builder_group='fake-group',
          ).assemble()),
      api.code_coverage(use_clang_coverage=True),
      api.post_process(post_process.MustRunRE, '.*coverage data.*'),
      api.post_process(
          post_process.MustRun,
          'process clang code coverage data for overall test coverage.'
          'gsutil Upload coverage artifacts'),
      api.post_process(post_process.DropExpectation),
  )
