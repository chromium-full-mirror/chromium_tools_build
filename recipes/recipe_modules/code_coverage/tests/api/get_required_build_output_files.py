# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process
from recipe_engine.recipe_api import Property

from RECIPE_MODULES.build import chromium_swarming
from RECIPE_MODULES.build.chromium_tests import steps
from RECIPE_MODULES.build.code_coverage import constants

DEPS = [
    'chromium',
    'chromium_tests',
    'chromium_tests_builder_config',
    'code_coverage',
    'profiles',
    'siso',
    'recipe_engine/assertions',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/step',
]

PROPERTIES = {
    'expected_paths': Property(kind=list),
    'target_platform': Property(kind=str)
}


def RunSteps(api, expected_paths, target_platform):
  api.chromium.set_config('chromium', TARGET_PLATFORM=target_platform)

  tests = [
      steps.SwarmingGTestTestSpec.create('browser_tests').get_test(
          api.chromium_tests),
      steps.SwarmingGTestTestSpec.create('android_browsertests').get_test(
          api.chromium_tests)
  ]

  api.code_coverage.source_dir = api.path.cleanup_dir
  api.code_coverage.build_dir = api.path.cleanup_dir.joinpath('out', 'Release')
  file_paths = api.code_coverage.get_required_build_output_files(tests)

  str_file_paths = [str(f) for f in file_paths]
  str_expected_paths = [str(f) for f in expected_paths]
  api.assertions.assertCountEqual(str_file_paths, str_expected_paths)


def GenTests(api):

  yield api.test(
      'basic',
      api.chromium.try_build(builder='linux-rel'),
      api.properties(
          expected_paths=[
              api.path.cleanup_dir / 'out/Release/browser_tests',
              api.path.cleanup_dir.joinpath(
                  'out/Release/binary_relative_paths_for_clang_code_coverage.json'
              ),
          ],
          target_platform='linux'),
      api.path.exists(api.path.cleanup_dir / 'out/Release/browser_tests'),
      api.post_process(post_process.DropExpectation),
  )

  android_test_path = (
      'out/Release/lib.unstripped/libandroid_browsertests__library.so')
  jacoco_file = ('broker_java__process_host__jacoco_sources.json')

  yield api.test(
      'android',
      api.chromium.try_build(
          builder_group='tryserver.chromium.android',
          builder='android-12-x64-rel'),
      api.code_coverage(use_java_coverage=True),
      api.properties(
          expected_paths=[
              api.path.cleanup_dir / android_test_path,
              api.path.cleanup_dir.joinpath(
                  'out/Release/binary_relative_paths_for_clang_code_coverage.json'
              ),
              api.path.cleanup_dir.joinpath(
                  'out/Release/{}'.format(jacoco_file)),
              api.path.cleanup_dir.joinpath('out/Release/{}'.format(
                  'chrome/browser/java__process_device.filter.jar'))
          ],
          target_platform='android'),
      api.path.exists(api.path.cleanup_dir / android_test_path),
      api.override_step_data(
          'Get all unstripped artifacts paths',
          api.json.output([f'[CLEANUP]/{android_test_path}'])),
      api.override_step_data(
          'Get jacoco and jar files for java coverage',
          api.json.output([
              f'[CLEANUP]/out/Release/{jacoco_file}',
              ('[CLEANUP]/out/Release/chrome/browser/'
               'java__process_device.filter.jar'),
          ])),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'siso_without_bytes',
      api.chromium.try_build(builder='linux-rel'),
      api.siso.properties(
          project='rbe-chromium-untrusted', output_local_strategy='minimum'),
      api.properties(
          expected_paths=[
              api.path.cleanup_dir / 'out/Release/browser_tests',
              api.path.cleanup_dir / 'out/Release/android_browsertests',
              api.path.cleanup_dir.joinpath(
                  'out/Release/binary_relative_paths_for_clang_code_coverage.json'
              ),
          ],
          target_platform='linux'),
      api.post_process(post_process.DropExpectation),
  )
