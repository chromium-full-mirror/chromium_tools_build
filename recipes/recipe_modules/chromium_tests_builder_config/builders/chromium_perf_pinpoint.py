# Copyright 2023 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from .. import builder_spec

SPEC = {}


def _common_kwargs(execution_mode, config_name, platform, target_bits):
  spec = {
      'execution_mode':
          execution_mode,
      'chromium_config':
          config_name,
      'chromium_config_kwargs': {
          'BUILD_CONFIG': 'Release',
          'TARGET_BITS': target_bits,
      },
      'gclient_config':
          config_name,
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

  return spec


def _BuildSpec(config_name,
               platform,
               target_bits,
               cros_boards=None,
               target_arch=None,
               extra_gclient_apply_config=None):

  kwargs = _common_kwargs(
      execution_mode=builder_spec.COMPILE_AND_TEST,
      config_name=config_name,
      platform=platform,
      target_bits=target_bits,
  )

  kwargs['perf_isolate_upload'] = True

  if target_arch:
    kwargs['chromium_config_kwargs']['TARGET_ARCH'] = target_arch

  kwargs['gclient_apply_config'] += [
      'checkout_pgo_profiles',
      'chromium_with_telemetry_dependencies',
  ]

  return builder_spec.BuilderSpec.create(**kwargs)


def _TestSpec(config_name,
              platform,
              target_bits,
              parent_buildername,
              cros_boards=None,
              target_arch=None):
  kwargs = _common_kwargs(
      execution_mode=builder_spec.TEST,
      config_name=config_name,
      platform=platform,
      target_bits=target_bits,
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
  spec = _TestSpec(
      'chromium_perf',
      platform,
      target_bits,
      parent_buildername=parent_buildername,
      cros_boards=cros_boards,
      target_arch=target_arch)
  SPEC[name] = spec


# Similar to _AddIsolatedTestSpec, except the builder is only available on
# Pinpoint, and not on perf waterfall.
def _AddPinpointTestSpec(name,
                         platform,
                         parent_buildername,
                         target_bits=64,
                         target_arch=None,
                         cros_boards=None):
  spec = _TestSpec(
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
  SPEC[name] = _BuildSpec(
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

# LUCI builder
_AddBuildSpec(
    'android_arm64_high_end-builder-perf-pgo', 'android', target_bits=64)

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

_AddBuildSpec('linux-builder-perf', 'linux')
_AddBuildSpec('linux-builder-perf-pgo', 'linux')

_AddIsolatedTestSpec('android-pixel4-perf', 'android',
                     'android_arm64-builder-perf')
_AddIsolatedTestSpec('android-pixel4_webview-perf', 'android',
                     'android_arm64-builder-perf')
_AddPinpointTestSpec('android-pixel4_webview-perf-pgo', 'android',
                     'android_arm64_high_end-builder-perf-pgo')

_AddIsolatedTestSpec('android-pixel4a_power-perf', 'android',
                     'android_arm64-builder-perf')

_AddIsolatedTestSpec('android-pixel-fold-perf', 'android',
                     'android_arm64_high_end-builder-perf')

_AddIsolatedTestSpec('android-pixel-tangor-perf-cbb', 'android',
                     'android_arm64_high_end-builder-perf')

# Pixel 9
_AddIsolatedTestSpec('android-pixel9-perf', 'android',
                     'android_arm64_high_end-builder-perf')
_AddIsolatedTestSpec('android-pixel9-pro-perf', 'android',
                     'android_arm64_high_end-builder-perf')
_AddIsolatedTestSpec('android-pixel9-pro-xl-perf', 'android',
                     'android_arm64_high_end-builder-perf')

# Pixel 10
_AddIsolatedTestSpec('android-pixel10-perf', 'android',
                     'android_arm64_high_end-builder-perf')
_AddIsolatedTestSpec('android-pixel10_webview-perf', 'android',
                     'android_arm64-builder-perf')
_AddPinpointTestSpec('android-pixel10_webview-perf-pgo', 'android',
                     'android_arm64_high_end-builder-perf-pgo')

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
_AddIsolatedTestSpec('win-11_laptop_mid_end-perf', 'win', 'win64-builder-perf')
_AddIsolatedTestSpec('win-gpu-perf', 'win', 'win64-builder-perf')
_AddIsolatedTestSpec('win-victus-perf-cbb', 'win', 'win64-builder-perf')
_AddIsolatedTestSpec(
    'win-arm64-snapdragon-plus-perf',
    'win',
    'win-arm64-builder-perf',
    target_arch='arm')
_AddIsolatedTestSpec(
    'win-arm64-snapdragon-plus-perf-cbb',
    'win',
    'win-arm64-builder-perf',
    target_arch='arm')
_AddIsolatedTestSpec(
    'win-arm64-snapdragon-elite-perf-cbb',
    'win',
    'win-arm64-builder-perf',
    target_arch='arm')
_AddIsolatedTestSpec(
    'win-arm64-snapdragon-elite-perf',
    'win',
    'win-arm64-builder-perf',
    target_arch='arm')

_AddIsolatedTestSpec('mac-intel-perf', 'mac', 'mac-builder-perf')
_AddIsolatedTestSpec(
    'mac-m1_mini_2020-perf', 'mac', 'mac-arm-builder-perf', target_arch='arm')
_AddIsolatedTestSpec(
    'mac-m1_mini_2020-perf-pgo',
    'mac',
    'mac-arm-builder-perf-pgo',
    target_arch='arm')
_AddIsolatedTestSpec(
    'mac-m2-pro-perf', 'mac', 'mac-arm-builder-perf', target_arch='arm')
_AddIsolatedTestSpec(
    'mac-m3-pro-perf-cbb', 'mac', 'mac-arm-builder-perf', target_arch='arm')
_AddIsolatedTestSpec(
    'mac-m3-pro-perf', 'mac', 'mac-arm-builder-perf', target_arch='arm')
_AddIsolatedTestSpec(
    'mac-m4-mini-perf', 'mac', 'mac-arm-builder-perf', target_arch='arm')
_AddIsolatedTestSpec(
    'mac-m4-pro-perf', 'mac', 'mac-arm-builder-perf', target_arch='arm')
_AddIsolatedTestSpec(
    'mac-m5-pro-perf', 'mac', 'mac-arm-builder-perf', target_arch='arm')

_AddIsolatedTestSpec('linux-r350-perf', 'linux', 'linux-builder-perf')
_AddIsolatedTestSpec('linux-perf', 'linux', 'linux-builder-perf')

# Deprecated in perf waterfall. Needed for pinpoint when running Chrome
# Health on old commits.
_AddPinpointTestSpec('mac-10_12_laptop_low_end-perf', 'mac', 'mac-builder-perf')

# Pinpoint-only bots
# android
_AddPinpointTestSpec('android-pixel4-perf-pgo', 'android',
                     'android_arm64-builder-perf-pgo')
_AddPinpointTestSpec('android-pixel4a_power-perf-pgo', 'android',
                     'android_arm64-builder-perf-pgo')
_AddPinpointTestSpec('android-pixel6-perf-pgo', 'android',
                     'android_arm64_high_end-builder-perf-pgo')
_AddPinpointTestSpec('android-pixel6-pro-perf-pgo', 'android',
                     'android_arm64_high_end-builder-perf-pgo')
_AddPinpointTestSpec('android-pixel9-perf', 'android',
                     'android_arm64_high_end-builder-perf-pgo')
_AddPinpointTestSpec('android-pixel9-pro-perf', 'android',
                     'android_arm64_high_end-builder-perf-pgo')
_AddPinpointTestSpec('android-pixel9-pro-xl-perf', 'android',
                     'android_arm64_high_end-builder-perf-pgo')
_AddPinpointTestSpec('android-new-pixel-perf', 'android',
                     'android_arm64-builder-perf')
_AddPinpointTestSpec('android-new-pixel-pro-perf', 'android',
                     'android_arm64-builder-perf')
_AddPinpointTestSpec('android-new-pixel-perf-pgo', 'android',
                     'android_arm64-builder-perf-pgo')
_AddPinpointTestSpec('android-new-pixel-pro-perf-pgo', 'android',
                     'android_arm64-builder-perf-pgo')
_AddPinpointTestSpec('android-samsung-foldable-perf', 'android',
                     'android_arm64-builder-perf')
_AddPinpointTestSpec('android-samsung-foldable-perf-pgo', 'android',
                     'android_arm64-builder-perf-pgo')
# linux
_AddPinpointTestSpec('linux-perf-pgo', 'linux', 'linux-builder-perf-pgo')
# mac
_AddPinpointTestSpec(
    'mac-m1_mini_2020-perf-pgo',
    'mac',
    'mac-arm-builder-perf-pgo',
    target_arch='arm')
# windows
_AddPinpointTestSpec('win-10-perf-pgo', 'win', 'win64-builder-perf-pgo')
_AddPinpointTestSpec('win-10_laptop_low_end-perf-pgo', 'win',
                     'win64-builder-perf-pgo')
_AddPinpointTestSpec('win-10_amd_laptop-perf-pgo', 'win',
                     'win64-builder-perf-pgo')
_AddPinpointTestSpec('win-11-perf-pgo', 'win', 'win64-builder-perf-pgo')
