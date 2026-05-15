# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine.post_process import DropExpectation, StepCommandContains

DEPS = [
    'chromium',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
]


def RunSteps(api):
  api.chromium.set_config(
      api.properties.get('chromium_config', 'chromium'),
      BUILD_CONFIG=api.properties.get('build_config', 'Release'),
      TARGET_PLATFORM=api.properties.get('target_platform', 'linux'))
  source_dir = api.path.cache_dir / 'builder/src'
  build_dir = api.chromium.default_build_dir(source_dir)

  use_remoteexec = api.properties.get('use_remoteexec', False)
  extra_args = api.properties.get('extra_args')

  api.chromium.run_gn(
      source_dir,
      build_dir,
      gn_path=api.properties.get('gn_path'),
      use_remoteexec=use_remoteexec,
      extra_args=extra_args)


def GenTests(api):
  yield api.test(
      'basic',
      api.chromium.ci_build(),
      api.post_process(StepCommandContains, 'gn', [
          'python3',
          'RECIPE_REPO[depot_tools]/gn.py',
      ]),
      api.post_process(StepCommandContains, 'gn', [
          '[CACHE]/builder/src/out/2796-Linux_Builder',
          '--args=is_debug=false target_cpu="x64"',
      ]),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'custom_gn_path',
      api.chromium.ci_build(),
      api.properties(gn_path='some/other/path/gn'),
      api.post_process(StepCommandContains, 'gn', [
          'some/other/path/gn',
      ]),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'mac',
      api.chromium.ci_build(),
      api.platform('mac', 64),
      api.properties(target_platform='mac'),
      api.post_process(StepCommandContains, 'gn', [
          '[CACHE]/builder/src/out/2796-Linux_Builder',
          '--args=is_clang=true is_debug=false target_cpu="x64"',
      ]),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'android',
      api.chromium.ci_build(),
      api.properties(target_platform='android'),
      api.post_process(StepCommandContains, 'gn', [
          '[CACHE]/builder/src/out/2796-Linux_Builder',
          '--args=is_debug=false target_os="android" target_cpu="x64"',
      ]),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'debug',
      api.chromium.ci_build(),
      api.properties(build_config='Debug'),
      api.post_process(StepCommandContains, 'gn', [
          '[CACHE]/builder/src/out/2796-Linux_Builder',
          '--args=is_debug=true target_cpu="x64"',
      ]),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'remoteexec',
      api.chromium.ci_build(),
      api.properties(build_config='Debug', use_remoteexec=True),
      api.post_process(StepCommandContains, 'gn', [
          '[CACHE]/builder/src/out/2796-Linux_Builder',
          '--args=is_debug=true target_cpu="x64" use_remoteexec=true',
      ]),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'extra_args',
      api.chromium.ci_build(),
      api.properties(extra_args=['--export-compile-commands']),
      api.post_process(StepCommandContains, 'gn', [
          '--export-compile-commands',
      ]),
      api.post_process(DropExpectation),
  )
