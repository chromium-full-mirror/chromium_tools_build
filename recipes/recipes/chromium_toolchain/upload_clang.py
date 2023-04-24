# Copyright 2016 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process
from recipe_engine.engine_types import freeze

from RECIPE_MODULES.build import chromium

DEPS = [
    'chromium',
    'chromium_checkout',
    'depot_tools/depot_tools',
    'depot_tools/gsutil',
    'depot_tools/osx_sdk',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/runtime',
    'recipe_engine/step',
]

BUILDERS = {
    'tryserver.chromium.linux': {
        'builders': {
            'linux_upload_clang':
                chromium.BuilderSpec.create(
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Release',
                        'TARGET_PLATFORM': 'linux',
                        'TARGET_BITS': 64,
                    },
                    gclient_apply_config=[
                        # 'android' is required to build the Clang toolchain with
                        # proper AddressSanitizer prebuilts for Chrome on Android.
                        'android',

                        # Required to build the builtins.a for Fuchsia.
                        'fuchsia_no_hooks',
                    ],
                ),
        },
    },
    'tryserver.chromium.mac': {
        'builders': {
            'mac_upload_clang':
                chromium.BuilderSpec.create(
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Release',
                        'TARGET_PLATFORM': 'mac',
                        'TARGET_BITS': 64,
                    },
                    gclient_apply_config=[
                        # Required to build the builtins.a for Fuchsia.
                        'fuchsia_no_hooks',
                    ],
                ),
            'mac_upload_clang_arm':
                chromium.BuilderSpec.create(
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Release',
                        'TARGET_PLATFORM': 'mac',
                        'TARGET_BITS': 64,
                    },
                    gclient_apply_config=[
                        # Required to build the builtins.a for Fuchsia.
                        'fuchsia_no_hooks',
                    ],
                ),
        },
    },
    'tryserver.chromium.win': {
        'builders': {
            'win_upload_clang':
                chromium.BuilderSpec.create(
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Release',
                        'TARGET_PLATFORM': 'win',
                        'TARGET_BITS': 32,
                    },),
        },
    },
}

# Copy bot config for toolchain packagers.
BUILDERS['official.toolchain'] = {
    'builders': {
        'toolchain-packager-linux':
            BUILDERS['tryserver.chromium.linux']['builders']
            ['linux_upload_clang'],
        'toolchain-packager-mac':
            BUILDERS['tryserver.chromium.mac']['builders']['mac_upload_clang'],
        'toolchain-packager-mac-arm':
            BUILDERS['tryserver.chromium.mac']['builders']
            ['mac_upload_clang_arm'],
        'toolchain-packager-windows':
            BUILDERS['tryserver.chromium.win']['builders']['win_upload_clang'],
    },
}

BUILDERS = freeze(BUILDERS)

ARM_MAC_BUILDERS = (
    'mac_upload_clang_arm',
    'toolchain-packager-mac-arm',
)


def RunSteps(api):
  _, bot_config = api.chromium.configure_bot(BUILDERS)

  api.chromium_checkout.ensure_checkout(bot_config)

  api.step('update win toolchain', [
      'python3', api.path['checkout'].join('build', 'vs_toolchain.py'), 'update'
  ])

  with api.osx_sdk('ios'):
    with api.depot_tools.on_path():
      args = ['--upload']
      if api.buildbucket.builder_name in ARM_MAC_BUILDERS:
        args += ['--build-mac-arm']
      api.step('package clang', [
          'python3', api.path['checkout'].join('tools', 'clang', 'scripts',
                                               'package.py')
      ] + args)


def GenTests(api):
  yield api.test(
      'mac',
      api.platform.name('mac'),
      api.chromium.try_build(
          builder_group='tryserver.chromium.mac',
          builder='mac_upload_clang_arm'),
      api.post_process(post_process.MustRun, 'install xcode'),
      api.post_process(post_process.MustRun, 'select XCode'),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'linux',
      api.platform.name('linux'),
      api.chromium.try_build(
          builder_group='tryserver.chromium.linux',
          builder='linux_upload_clang'),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )
