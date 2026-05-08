# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

DEPS = [
    'chromium',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/runtime',
    'recipe_engine/step',
]


def RunSteps(api):
  api.chromium.set_config(
      'chromium',
      TARGET_PLATFORM=api.properties.get('target_platform', 'linux'),
      TARGET_CROS_BOARDS=api.properties.get('target_cros_boards'))
  if api.properties.get('clobber'):
    api.chromium.apply_config('clobber')
  api.chromium.apply_config('mb')

  source_dir = api.path.cache_dir / 'builder/src'
  build_dir = source_dir / 'out/Release'

  api.chromium.runhooks(source_dir, build_dir=build_dir)


def GenTests(api):

  yield api.test(
      'basic',
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'clobber',
      api.properties(clobber=True),
      api.post_process(post_process.StepSuccess, 'clobber'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'mac',
      api.platform.name('mac'),
      api.properties(target_platform='mac'),
      api.post_process(post_process.StepSuccess, 'ensure_installed'),
      api.post_process(post_process.StepCommandContains, 'ensure_installed', [
          'infra/tools/mac_toolchain/${platform} '
          'git_revision:b0c0a706097c27444dbe3f84e5553f1aaa77c1a6'
      ]),
      api.post_process(post_process.StepEnvContains, 'gclient runhooks',
                       {'FORCE_MAC_TOOLCHAIN': '1'}),
      api.post_process(post_process.StepEnvContains, 'gclient runhooks',
                       {'GYP_DEFINES': 'clang=1'}),
      api.post_process(
          post_process.StepEnvContains, 'gclient runhooks',
          {'MAC_TOOLCHAIN_INSTALLER': '[START_DIR]/mac_toolchain'}),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'with_mac_toolchain_version',
      api.platform.name('mac'),
      api.properties(target_platform='mac'),
      api.chromium.properties(mac_toolchain_version='custom_version'),
      api.post_process(post_process.StepSuccess, 'ensure_installed'),
      api.post_process(
          post_process.StepCommandContains, 'ensure_installed',
          ['infra/tools/mac_toolchain/${platform} git_revision:custom_version'
          ]),
      api.post_process(post_process.DropExpectation),
  )
