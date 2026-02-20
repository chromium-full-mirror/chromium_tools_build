# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from RECIPE_MODULES.build.chromium_tests_builder_config import (builder_db,
                                                                builder_spec)


def _angle_spec(**kwargs):
  kwargs.setdefault('chromium_config', 'angle_clang')
  return builder_spec.BuilderSpec.create(**kwargs)


def _create_builder_config(platform,
                           config,
                           target_bits,
                           is_clang=True,
                           perf_isolate_upload=False,
                           gclient_config='angle'):
  return _angle_spec(
      chromium_config='angle_clang' if is_clang else 'angle_non_clang',
      gclient_config=gclient_config,
      simulation_platform=platform,
      chromium_config_kwargs={
          'BUILD_CONFIG': config,
          'TARGET_BITS': target_bits,
      },
      perf_isolate_upload=perf_isolate_upload,
  )


def _create_tester_config(platform, target_bits, parent_builder):
  is_experimental = '-exp' in parent_builder
  return _angle_spec(
      gclient_config='angle',
      simulation_platform=platform,
      chromium_config_kwargs={
          # All testing is in Release.
          'BUILD_CONFIG': 'Release',
          'TARGET_BITS': target_bits,
      },
      execution_mode=builder_spec.TEST,
      parent_buildername=parent_builder,
      # Serialize tests on exp builders since they are short on resources
      serialize_tests=is_experimental,
  )


def _create_android_builder_config(config,
                                   target_bits,
                                   perf_isolate_upload=False):
  return _angle_spec(
      gclient_config='angle_android',
      simulation_platform='linux',
      chromium_config_kwargs={
          'BUILD_CONFIG': config,
          'TARGET_BITS': target_bits,
          'TARGET_PLATFORM': 'android',
      },
      perf_isolate_upload=perf_isolate_upload,
  )


def _create_android_tester_config(target_bits, parent_builder):
  is_experimental = '-exp' in parent_builder
  return _angle_spec(
      gclient_config='angle_android',
      simulation_platform='linux',
      chromium_config_kwargs={
          # All testing is in Release.
          'BUILD_CONFIG': 'Release',
          'TARGET_BITS': target_bits,
          'TARGET_PLATFORM': 'android',
      },
      execution_mode=builder_spec.TEST,
      parent_buildername=parent_builder,
      # Serialize tests on exp builders since they are short on resources
      serialize_tests=is_experimental,
  )


_SPEC = {
    'android-arm-compile':
        _create_android_builder_config('Release', 32),
    'android-arm-dbg-compile':
        _create_android_builder_config('Debug', 32),
    'android-arm64-dbg':
        _create_android_builder_config('Debug', 64),
    'android-arm64-dbg-compile':
        _create_android_builder_config('Debug', 64),
    'android-arm64-exp-pixel6':
        _create_android_tester_config(64, 'android-arm64-exp-test'),
    'android-arm64-exp-pixel10':
        _create_android_tester_config(64, 'android-arm64-exp-pixel10-test'),
    'android-arm64-exp-pixel10-test':
        _create_android_builder_config('Release', 64),
    'android-arm64-exp-s24':
        _create_android_tester_config(64, 'android-arm64-exp-s24-test'),
    'android-arm64-exp-s24-test':
        _create_android_builder_config('Release', 64),
    'android-arm64-exp-test':
        _create_android_builder_config('Release', 64),
    'android-arm64-ir-pixel4':
        _create_android_tester_config(64, 'android-arm64-ir-test'),
    'android-arm64-ir-pixel6':
        _create_android_tester_config(64, 'android-arm64-ir-test'),
    'android-arm64-ir-test':
        _create_android_builder_config('Release', 64),
    'android-arm64-pixel4':
        _create_android_tester_config(64, 'android-arm64-test'),
    'android-arm64-pixel4-perf':
        _create_android_tester_config(64, 'android-perf'),
    'android-arm64-pixel6':
        _create_android_tester_config(64, 'android-arm64-test'),
    'android-arm64-pixel6-perf':
        _create_android_tester_config(64, 'android-perf'),
    'android-arm64-test':
        _create_android_builder_config('Release', 64),
    'android-perf':
        _create_android_builder_config('Release', 64, perf_isolate_upload=True),
    'linux-amd':
        _create_tester_config('linux', 64, 'linux-test'),
    'linux-asan-test':
        _create_builder_config('linux', 'Release', 64),
    'linux-dbg-compile':
        _create_builder_config('linux', 'Debug', 64),
    'linux-exp-asan-test':
        _create_builder_config('linux', 'Release', 64),
    'linux-exp-intel':
        _create_tester_config('linux', 64, 'linux-exp-test'),
    'linux-exp-nvidia':
        _create_tester_config('linux', 64, 'linux-exp-test'),
    'linux-exp-swiftshader':
        _create_tester_config('linux', 64, 'linux-exp-test'),
    'linux-exp-swiftshader-asan':
        _create_tester_config('linux', 64, 'linux-exp-asan-test'),
    'linux-exp-swiftshader-tsan':
        _create_tester_config('linux', 64, 'linux-exp-tsan-test'),
    'linux-exp-test':
        _create_builder_config('linux', 'Release', 64),
    'linux-exp-tsan-test':
        _create_builder_config('linux', 'Release', 64),
    'linux-intel-perf':
        _create_tester_config('linux', 64, 'linux-perf'),
    'linux-ir-amd':
        _create_tester_config('linux', 64, 'linux-ir-test'),
    'linux-ir-intel':
        _create_tester_config('linux', 64, 'linux-ir-test'),
    'linux-ir-nvidia':
        _create_tester_config('linux', 64, 'linux-ir-test'),
    'linux-ir-swiftshader':
        _create_tester_config('linux', 64, 'linux-ir-test'),
    'linux-ir-test':
        _create_builder_config('linux', 'Release', 64),
    'linux-nvidia-perf':
        _create_tester_config('linux', 64, 'linux-perf'),
    'linux-perf':
        _create_builder_config(
            'linux', 'Release', 64, perf_isolate_upload=True),
    'linux-swiftshader-asan':
        _create_tester_config('linux', 64, 'linux-asan-test'),
    'linux-swiftshader-tsan':
        _create_tester_config('linux', 64, 'linux-tsan-test'),
    'linux-test':
        _create_builder_config('linux', 'Release', 64),
    'linux-tsan-test':
        _create_builder_config('linux', 'Release', 64),
    'mac-amd':
        _create_tester_config('mac', 64, 'mac-test'),
    'mac-arm64-apple':
        _create_tester_config('mac', 64, 'mac-arm64-test'),
    'mac-arm64-test':
        _create_builder_config('mac', 'Release', 64),
    'mac-dbg-compile':
        _create_builder_config('mac', 'Debug', 64),
    'mac-exp-amd':
        _create_tester_config('mac', 64, 'mac-exp-test'),
    'mac-exp-intel':
        _create_tester_config('mac', 64, 'mac-exp-test'),
    'mac-exp-test':
        _create_builder_config('mac', 'Release', 64),
    'mac-intel':
        _create_tester_config('mac', 64, 'mac-test'),
    'mac-ir-amd':
        _create_tester_config('mac', 64, 'mac-ir-test'),
    'mac-ir-intel':
        _create_tester_config('mac', 64, 'mac-ir-test'),
    'mac-ir-test':
        _create_builder_config('mac', 'Release', 64),
    'mac-test':
        _create_builder_config('mac', 'Release', 64),
    'mac-x64-amd-555x-test':
        _create_tester_config('mac', 64, 'mac-test'),
    'win-asan-test':
        _create_builder_config('win', 'Release', 64),
    'win-dbg-compile':
        _create_builder_config('win', 'Debug', 64),
    'win-exp-test':
        _create_builder_config('win', 'Release', 64),
    'win-ir-test':
        _create_builder_config('win', 'Release', 64),
    'win-msvc-compile':
        _create_builder_config('win', 'Release', 64, is_clang=False),
    'win-msvc-dbg-compile':
        _create_builder_config('win', 'Debug', 64, is_clang=False),
    'win-msvc-x86-compile':
        _create_builder_config('win', 'Release', 32, is_clang=False),
    'win-msvc-x86-dbg-compile':
        _create_builder_config('win', 'Debug', 32, is_clang=False),
    'win-perf':
        _create_builder_config('win', 'Release', 64, perf_isolate_upload=True),
    'win-test':
        _create_builder_config('win', 'Release', 64),
    'win-trace':
        _create_builder_config(
            'win', 'Release', 64, gclient_config='angle_nointernal'),
    'win-x86-dbg-compile':
        _create_builder_config('win', 'Debug', 32),
    'win-x86-test':
        _create_builder_config('win', 'Release', 32),
    'winuwp-compile':
        _create_builder_config('win', 'Release', 64, is_clang=False),
    'winuwp-dbg-compile':
        _create_builder_config('win', 'Debug', 64, is_clang=False),
    'win10-x64-exp-intel':
        _create_tester_config('win', 64, 'win-exp-test'),
    'win10-x64-exp-nvidia':
        _create_tester_config('win', 64, 'win-exp-test'),
    'win10-x64-intel':
        _create_tester_config('win', 64, 'win-test'),
    'win10-x64-intel-perf':
        _create_tester_config('win', 64, 'win-perf'),
    'win10-x64-ir-intel':
        _create_tester_config('win', 64, 'win-ir-test'),
    'win10-x64-ir-nvidia':
        _create_tester_config('win', 64, 'win-ir-test'),
    'win10-x64-nvidia':
        _create_tester_config('win', 64, 'win-test'),
    'win10-x64-nvidia-perf':
        _create_tester_config('win', 64, 'win-perf'),
    'win10-x64-swiftshader-asan':
        _create_tester_config('win', 64, 'win-asan-test'),
    'win10-x86-swiftshader':
        _create_tester_config('win', 32, 'win-x86-test'),
}

BUILDERS = builder_db.BuilderDatabase.create({
    'angle': _SPEC,
})
