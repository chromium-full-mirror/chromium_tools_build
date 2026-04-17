# Copyright 2015 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from .. import builder_spec

from RECIPE_MODULES.build.chromium import CONFIG_CTX as CHROMIUM_CONFIG_CTX

SPEC = {}


@CHROMIUM_CONFIG_CTX(includes=[
    'chromium',
    'mb',
    'official',
])
def chromium_perf(c):
  c.clobber_before_runhooks = False


@CHROMIUM_CONFIG_CTX(includes=[
    'chromium',
    'mb',
])
def chromium_public_perf(c):
  c.clobber_before_runhooks = False


def _common_kwargs(execution_mode, chromium_config, platform, target_bits,
                   gclient_config):
  spec = {
      'execution_mode':
          execution_mode,
      'chromium_config':
          chromium_config,
      'chromium_config_kwargs': {
          'BUILD_CONFIG': 'Release',
          'TARGET_BITS': target_bits,
      },
      'gclient_config':
          gclient_config,
      'gclient_apply_config': [],
      'simulation_platform':
          'linux' if platform in ('android', 'chromeos',
                                  'fuchsia') else platform,
  }

  if platform == 'android':
    spec['android_config'] = 'base_config'
    spec['android_apply_config'] = ['use_devil_adb']
    spec['chromium_apply_config'] = ['android', 'android_internal_isolate_maps']
    spec['chromium_config_kwargs']['TARGET_ARCH'] = 'arm'
    spec['chromium_config_kwargs']['TARGET_PLATFORM'] = 'android'
    spec['gclient_apply_config'] += ['android']
  elif platform == 'chromeos':
    spec['chromium_config_kwargs']['TARGET_PLATFORM'] = 'chromeos'
    spec['gclient_apply_config'] += ['chromeos']
  elif platform == 'fuchsia':
    spec['chromium_config_kwargs']['TARGET_PLATFORM'] = 'fuchsia'

  return spec


def BuildSpec(
    config_name,
    platform,
    target_bits,
    cros_boards=None,
    target_arch=None,
    extra_gclient_apply_config=None,
    chromium_config=None,
    gclient_config=None,
):

  kwargs = _common_kwargs(
      execution_mode=builder_spec.COMPILE_AND_TEST,
      chromium_config=chromium_config or config_name,
      platform=platform,
      target_bits=target_bits,
      gclient_config=gclient_config or config_name,
  )

  kwargs['perf_isolate_upload'] = True

  if cros_boards:
    kwargs['chromium_config_kwargs']['TARGET_CROS_BOARDS'] = cros_boards

  if target_arch:
    kwargs['chromium_config_kwargs']['TARGET_ARCH'] = target_arch

  kwargs['gclient_apply_config'] += [
      'checkout_pgo_profiles',
      'chromium_with_telemetry_dependencies',
  ]
  if extra_gclient_apply_config:
    kwargs['gclient_apply_config'] += list(extra_gclient_apply_config)

  return builder_spec.BuilderSpec.create(**kwargs)


def TestSpec(config_name,
             platform,
             target_bits,
             parent_buildername,
             cros_boards=None,
             target_arch=None,
             chromium_config=None,
             gclient_config=None):
  kwargs = _common_kwargs(
      execution_mode=builder_spec.TEST,
      chromium_config=chromium_config or config_name,
      platform=platform,
      target_bits=target_bits,
      gclient_config=gclient_config or config_name,
  )

  kwargs['parent_buildername'] = parent_buildername
  kwargs['gclient_apply_config'].append('chromium_skip_wpr_archives_download')

  if cros_boards:
    kwargs['chromium_config_kwargs']['TARGET_CROS_BOARDS'] = cros_boards

  if target_arch:
    kwargs['chromium_config_kwargs']['TARGET_ARCH'] = target_arch

  return builder_spec.BuilderSpec.create(**kwargs)


def _AddIsolatedTestSpec(name,
                         platform,
                         parent_buildername,
                         target_bits=64,
                         target_arch=None,
                         cros_boards=None):
  spec = TestSpec(
      'chromium_perf',
      platform,
      target_bits,
      parent_buildername=parent_buildername,
      cros_boards=cros_boards,
      target_arch=target_arch)
  SPEC[name] = spec


def _AddBuildSpec(name,
                  platform,
                  target_bits=64,
                  target_arch=None,
                  gclient_apply_config=None):
  SPEC[name] = BuildSpec(
      'chromium_perf',
      platform,
      target_bits,
      target_arch=target_arch,
      extra_gclient_apply_config=gclient_apply_config)


# LUCI builder
_AddBuildSpec('android-builder-perf', 'android', target_bits=32)

# LUCI builder
_AddBuildSpec('android-builder-perf-pgo', 'android', target_bits=32)

# LUCI builder
_AddBuildSpec('android_arm64-builder-perf', 'android', target_bits=64)

_AddBuildSpec('android_arm64-builder-perf-pgo', 'android', target_bits=64)

_AddBuildSpec('android_arm64_high_end-builder-perf', 'android', target_bits=64)
_AddBuildSpec('android-desktop-x64-builder-perf', 'android', target_bits=64)
_AddBuildSpec('android-desktop-arm-builder-perf', 'android', target_bits=64)

# LUCI builder
# The config for the following builders is now specified src-side in
# //internal/infra/config/subprojects/chrome/ci/chromium.perf.star
# * android_arm64_high_end-builder-perf-pgo
# * android-pixel4_webview-perf-pgo
# * android-pixel6-perf-pgo

_AddBuildSpec('win64-builder-perf', 'win')
_AddBuildSpec('win64-builder-perf-pgo', 'win')
_AddBuildSpec('win-arm64-builder-perf', 'win', target_arch='arm')
_AddBuildSpec('mac-builder-perf', 'mac')
_AddBuildSpec('mac-builder-perf-pgo', 'mac')
_AddBuildSpec(
    'mac-arm-builder-perf',
    'mac',
    target_arch='arm',
)
_AddBuildSpec(
    'mac-arm-builder-perf-pgo',
    'mac',
    target_arch='arm',
)
_AddBuildSpec(
    'mac-arm-no-brp-builder-perf',
    'mac',
    target_arch='arm',
)

_AddBuildSpec('linux-builder-perf', 'linux')
_AddBuildSpec('linux-builder-perf-pgo', 'linux')
_AddBuildSpec('linux-builder-perf-rel', 'linux')


_AddIsolatedTestSpec('android-pixel4-perf', 'android',
                     'android_arm64-builder-perf')
_AddIsolatedTestSpec('android-pixel4_webview-perf', 'android',
                     'android_arm64-builder-perf')

_AddIsolatedTestSpec('android-pixel6-perf', 'android',
                     'android_arm64_high_end-builder-perf')
_AddIsolatedTestSpec('android-pixel6-pro-perf', 'android',
                     'android_arm64_high_end-builder-perf')
_AddIsolatedTestSpec('android-pixel-fold-perf', 'android',
                     'android_arm64_high_end-builder-perf')
_AddIsolatedTestSpec('android-pixel-tangor-perf', 'android',
                     'android_arm64_high_end-builder-perf')

# Pixel 9
_AddIsolatedTestSpec('android-pixel9-perf', 'android',
                     'android_arm64_high_end-builder-perf')
_AddIsolatedTestSpec('android-pixel9-pro-perf', 'android',
                     'android_arm64_high_end-builder-perf')
_AddIsolatedTestSpec('android-pixel9-pro-xl-perf', 'android',
                     'android_arm64_high_end-builder-perf')

# Pixel 2025
_AddIsolatedTestSpec('android-pixel25-ultra-perf', 'android',
                     'android_arm64_high_end-builder-perf')
_AddIsolatedTestSpec('android-pixel25-ultra-xl-perf', 'android',
                     'android_arm64_high_end-builder-perf')

# AL
_AddIsolatedTestSpec('android-brya-kano-i5-8gb-perf', 'android',
                     'android-desktop-x64-builder-perf')
_AddIsolatedTestSpec('android-corsola-steelix-8gb-perf', 'android',
                     'android-desktop-arm-builder-perf')
_AddIsolatedTestSpec('android-nissa-uldren-8gb-perf', 'android',
                     'android-desktop-x64-builder-perf')

_AddIsolatedTestSpec(
    'android-go-wembley-perf',
    'android',
    'android-builder-perf',
    target_bits=32)

_AddIsolatedTestSpec(
    'android-go-wembley_webview-perf',
    'android',
    'android-builder-perf',
    target_bits=32)

_AddIsolatedTestSpec('win-10-perf', 'win', 'win64-builder-perf')
_AddIsolatedTestSpec('win-10_laptop_low_end-perf', 'win', 'win64-builder-perf')
_AddIsolatedTestSpec('win-10_amd_laptop-perf', 'win', 'win64-builder-perf')
_AddIsolatedTestSpec('win-11-perf', 'win', 'win64-builder-perf')
_AddIsolatedTestSpec(
    'win-arm64-snapdragon-elite-perf',
    'win',
    'win-arm64-builder-perf',
    target_arch='arm')

_AddIsolatedTestSpec('mac-intel-perf', 'mac', 'mac-builder-perf')
_AddIsolatedTestSpec(
    'mac-m1_mini_2020-perf', 'mac', 'mac-arm-builder-perf', target_arch='arm')
_AddIsolatedTestSpec(
    'mac-m1_mini_2020-perf-pgo', 'mac', 'mac-arm-builder-perf-pgo', target_arch='arm')
_AddIsolatedTestSpec(
    'mac-m1_mini_2020-no-brp-perf',
    'mac',
    'mac-arm-no-brp-builder-perf',
    target_arch='arm')
_AddIsolatedTestSpec(
    'mac-m1-pro-perf', 'mac', 'mac-arm-builder-perf', target_arch='arm')
_AddIsolatedTestSpec(
    'mac-m2-pro-perf', 'mac', 'mac-arm-builder-perf', target_arch='arm')
_AddIsolatedTestSpec(
    'mac-m3-pro-perf', 'mac', 'mac-arm-builder-perf', target_arch='arm')
_AddIsolatedTestSpec(
    'mac-m4-mini-perf', 'mac', 'mac-arm-builder-perf', target_arch='arm')
_AddIsolatedTestSpec(
    'mac-m5-pro-perf', 'mac', 'mac-arm-builder-perf', target_arch='arm')

_AddIsolatedTestSpec('linux-r350-perf', 'linux', 'linux-builder-perf')
_AddIsolatedTestSpec('linux-perf', 'linux', 'linux-builder-perf')
_AddIsolatedTestSpec('linux-perf-rel', 'linux', 'linux-builder-perf-rel')


# Perf result processors
_AddIsolatedTestSpec('linux-r350-processor-perf', 'linux', 'linux-r350-perf')

_AddIsolatedTestSpec(
    'mac-m4-mini-processor-perf', 'mac', 'mac-m4-mini-perf', target_arch='arm')

_AddIsolatedTestSpec('win-10-processor-perf', 'win', 'win-10-perf')
_AddIsolatedTestSpec('win-10_laptop_low_end-processor-perf', 'win',
                     'win-10_laptop_low_end-perf')
_AddIsolatedTestSpec('win-11-processor-perf', 'win', 'win-11-perf')
