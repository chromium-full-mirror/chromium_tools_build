# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process
from recipe_engine import recipe_api

DEPS = [
    'chromium',
    'chromium_tests',
    'chromium_tests_builder_config',
    'code_coverage',
    'orderfile',
    'profiles',
    'recipe_engine/buildbucket',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/step',
]


def RunSteps(api: recipe_api.RecipeApi):
  source_dir = api.path.cache_dir / 'builder/src'

  _, builder_config = api.chromium_tests_builder_config.lookup_builder()

  api.chromium_tests.configure_build(builder_config)

  # Fake path.
  api.profiles.source_dir = api.path.start_dir

  api.orderfile.configure_custom_pgo_profile(source_dir)

  api.orderfile.process_orderfile_data(source_dir)

  # coverage only
  _ = api.orderfile.using_orderfile


def GenTests(api):

  yield api.test(
      'basic_android',
      api.chromium.generic_build(
          builder_group='chromium.perf', builder='android-builder-perf'),
      api.orderfile(use_orderfile=True, upload_orderfile=True),
      api.platform('linux', 32),
      api.post_process(
          post_process.MustRun,
          'processing generated orderfile.uploading generated orderfile to CIPD'
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'basic_skip_android',
      api.chromium.generic_build(
          builder_group='chromium.perf', builder='android-builder-perf'),
      api.orderfile(use_orderfile=True),
      api.platform('linux', 32),
      api.post_process(
          post_process.MustRunRE,
          'processing generated orderfile.skipping upload to CIPD.*'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'gs_bucket_no_file',
      api.chromium.generic_build(
          builder_group='chromium.perf', builder='android-builder-perf'),
      api.orderfile(
          use_orderfile=True, gs_bucket="bucket", gs_bucket_path="path"),
      api.platform('linux', 32),
      api.post_process(post_process.MustRun, 'no custom PGO profile specified'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'gs_bucket_with_file',
      api.chromium.generic_build(
          builder_group='chromium.perf', builder='android-builder-perf'),
      api.orderfile(
          use_orderfile=True,
          gs_bucket="bucket",
          gs_bucket_path="path",
          last_uploaded_pgo_filename="profile.prof"),
      api.platform('linux', 32),
      api.post_process(post_process.MustRun, 'override PGO profile'),
      api.post_process(post_process.DropExpectation),
  )
