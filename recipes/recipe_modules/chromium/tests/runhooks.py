# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

DEPS = [
    'chromium',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/runtime',
]


def RunSteps(api):
  api.chromium.set_config(
      'chromium',
      TARGET_PLATFORM=api.properties.get('target_platform', 'linux'),
      TARGET_CROS_BOARDS=api.properties.get('target_cros_boards'))
  api.chromium.apply_config('mb')
  api.path.checkout_dir = api.path.cache_dir / 'builder' / 'src'

  api.chromium.runhooks()


def GenTests(api):

  yield api.test(
      'basic',
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'chromeos',
      api.properties(
          target_platform='chromeos', target_cros_boards='x86-generic'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'clobber',
      api.properties(clobber='1'),
      api.post_process(post_process.StepSuccess, 'clobber'),
      api.post_process(post_process.DropExpectation),
  )

  # TODO(b/256012263): Remove this when the fix has rolled in.
  yield api.test(
      'clobber_cros_cache_bug',
      api.properties(clobber='1'),
      api.path.exists(api.path.checkout_dir.joinpath('build', 'cros_cache')),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'mac',
      api.platform.name('mac'),
      api.properties(target_platform='mac'),
      api.post_process(post_process.StepSuccess, 'ensure_installed'),
      api.post_process(post_process.StepEnvContains, 'gclient runhooks',
                       {'FORCE_MAC_TOOLCHAIN': '1'}),
      api.post_process(post_process.StepEnvContains, 'gclient runhooks',
                       {'GYP_DEFINES': 'clang=1'}),
      api.post_process(
          post_process.StepEnvContains, 'gclient runhooks',
          {'MAC_TOOLCHAIN_INSTALLER': '[START_DIR]/mac_toolchain'}),
      api.post_process(post_process.DropExpectation),
  )
