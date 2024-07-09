# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

from RECIPE_MODULES.build.chromium_tests.api import ALL_TEST_BINARIES_ISOLATE_NAME

DEPS = [
    'isolate',
    'recipe_engine/path',
    'recipe_engine/properties',
]


def RunSteps(api):
  source_dir = api.path.cache_dir / 'builder' / 'src'
  browser_test_path = source_dir / 'out/Release/browser_tests'
  api.isolate.write_isolate_files_for_binary_file_paths(
      file_paths=[browser_test_path],
      isolate_target_name=ALL_TEST_BINARIES_ISOLATE_NAME,
      source_dir=source_dir,
      build_dir=source_dir / 'out/Release',
  )


def GenTests(api):
  yield api.test(
      'basic',
      api.post_process(post_process.MustRunRE,
                       r'.*{}.isolate'.format(ALL_TEST_BINARIES_ISOLATE_NAME)),
      api.post_process(
          post_process.MustRunRE,
          r'.*{}.isolated.gen.json'.format(ALL_TEST_BINARIES_ISOLATE_NAME)),
      api.post_process(post_process.DropExpectation),
  )
