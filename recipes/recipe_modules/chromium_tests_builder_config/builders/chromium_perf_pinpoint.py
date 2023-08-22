# Copyright 2023 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

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
    spec['android_config'] = 'chromium_perf'
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
               bisect_archive_build=False,
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

  kwargs['gclient_apply_config'] += ['checkout_pgo_profiles']

  kwargs['bisect_archive_build'] = bisect_archive_build
  if bisect_archive_build:
    # Bucket for storing builds for manual bisect
    kwargs['bisect_gs_bucket'] = 'chrome-test-builds'
    kwargs['bisect_gs_extra'] = 'official-by-commit'

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
                  bisect_archive_build=False,
                  target_arch=None,
                  gclient_apply_config=None):
  SPEC[name] = _BuildSpec(
      'chromium_perf',
      platform,
      target_bits,
      bisect_archive_build=bisect_archive_build,
      target_arch=target_arch,
      extra_gclient_apply_config=gclient_apply_config)


# LUCI builder
_AddBuildSpec(
    'android-builder-perf',
    'android',
    target_bits=32,
    bisect_archive_build=True)

# LUCI builder
_AddBuildSpec(
    'android-builder-perf-pgo',
    'android',
    target_bits=32,
    bisect_archive_build=True)

# LUCI builder
_AddBuildSpec(
    'android_arm64-builder-perf',
    'android',
    target_bits=64,
    bisect_archive_build=True)

_AddBuildSpec(
    'android_arm64-builder-perf-pgo',
    'android',
    target_bits=64,
    bisect_archive_build=True)

_AddBuildSpec(
    'android_arm64_high_end-builder-perf',
    'android',
    target_bits=64,
    bisect_archive_build=True)

# LUCI builder
_AddBuildSpec(
    'android_arm64_high_end-builder-perf-pgo',
    'android',
    target_bits=64,
    bisect_archive_build=True)

_AddBuildSpec('win64-builder-perf', 'win', bisect_archive_build=True)
_AddBuildSpec('win64-builder-perf-pgo', 'win', bisect_archive_build=True)
_AddBuildSpec('mac-builder-perf', 'mac', bisect_archive_build=True)
_AddBuildSpec('mac-builder-perf-pgo', 'mac', bisect_archive_build=True)
_AddBuildSpec(
    'mac-arm-builder-perf',
    'mac',
    bisect_archive_build=True,
    target_arch='arm',
)
_AddBuildSpec(
    'mac-arm-builder-perf-pgo',
    'mac',
    bisect_archive_build=True,
    target_arch='arm',
)

# Adapted from 'lacros-amd64-generic-chrome' and 'lacros-arm-generic-chrome'
# to measure binary size.
SPEC.update({
    'chromeos-amd64-generic-lacros-builder-perf':
        builder_spec.BuilderSpec.create(
            chromium_config='chromium_perf',
            gclient_apply_config=[
                'chromeos', 'checkout_lacros_sdk', 'checkout_pgo_profiles'
            ],
            gclient_config='chromium_perf',
            perf_isolate_upload=True,
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_ARCH': 'intel',
                'TARGET_BITS': 64,
                'TARGET_CROS_BOARDS': 'amd64-generic:eve:octopus',
                'TARGET_PLATFORM': 'chromeos',
            },
            simulation_platform='linux',
            bisect_archive_build=True,
            bisect_gs_bucket='chrome-test-builds',
            bisect_gs_extra='official-by-commit',
        ),
    'chromeos-arm-generic-lacros-builder-perf':
        builder_spec.BuilderSpec.create(
            chromium_config='chromium_perf',
            gclient_apply_config=[
                'chromeos', 'checkout_lacros_sdk', 'checkout_pgo_profiles'
            ],
            gclient_config='chromium_perf',
            perf_isolate_upload=True,
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_ARCH': 'arm',
                'TARGET_BITS': 32,
                'TARGET_CROS_BOARDS': 'arm-generic',
                'TARGET_PLATFORM': 'chromeos',
            },
            simulation_platform='linux',
            bisect_archive_build=True,
            bisect_gs_bucket='chrome-test-builds',
            bisect_gs_extra='official-by-commit',
        ),
    'chromeos-arm64-generic-lacros-builder-perf':
        builder_spec.BuilderSpec.create(
            chromium_config='chromium_perf',
            gclient_apply_config=[
                'chromeos', 'checkout_lacros_sdk', 'checkout_pgo_profiles'
            ],
            gclient_config='chromium_perf',
            perf_isolate_upload=True,
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_ARCH': 'arm',
                'TARGET_BITS': 64,
                'TARGET_CROS_BOARDS': 'arm64-generic',
                'TARGET_PLATFORM': 'chromeos',
            },
            simulation_platform='linux',
            bisect_archive_build=True,
            bisect_gs_bucket='chrome-test-builds',
            bisect_gs_extra='official-by-commit',
        ),
})

_AddBuildSpec('linux-builder-perf', 'linux', bisect_archive_build=True)
_AddBuildSpec('linux-builder-perf-pgo', 'linux', bisect_archive_build=True)

_AddBuildSpec(
    'chromecast-linux-builder-perf', 'linux', bisect_archive_build=True)

_AddIsolatedTestSpec(
    'android-go-perf', 'android', 'android-builder-perf', target_bits=32)
_AddIsolatedTestSpec(
    'android-go_webview-perf',
    'android',
    'android-builder-perf',
    target_bits=32)

_AddIsolatedTestSpec('android-pixel2-perf', 'android',
                     'android_arm64-builder-perf')
_AddIsolatedTestSpec('android-pixel2_webview-perf', 'android',
                     'android_arm64-builder-perf')

_AddIsolatedTestSpec('android-pixel4-perf', 'android',
                     'android_arm64-builder-perf')
_AddIsolatedTestSpec('android-pixel4_webview-perf', 'android',
                     'android_arm64-builder-perf')

_AddIsolatedTestSpec('android-pixel4a_power-perf', 'android',
                     'android_arm64-builder-perf')

_AddIsolatedTestSpec('android-pixel6-perf', 'android',
                     'android_arm64_high_end-builder-perf')
_AddIsolatedTestSpec('android-pixel6-pro-perf', 'android',
                     'android_arm64_high_end-builder-perf')

_AddIsolatedTestSpec(
    'android-go-wembley-perf',
    'android',
    'android-builder-perf',
    target_bits=32)

_AddIsolatedTestSpec('win-10-perf', 'win', 'win64-builder-perf')
_AddIsolatedTestSpec('win-10_laptop_low_end-perf', 'win', 'win64-builder-perf')
_AddIsolatedTestSpec('win-10_amd_laptop-perf', 'win', 'win64-builder-perf')

_AddIsolatedTestSpec('mac-laptop_low_end-perf', 'mac', 'mac-builder-perf')
_AddIsolatedTestSpec('mac-laptop_high_end-perf', 'mac', 'mac-builder-perf')
_AddIsolatedTestSpec(
    'mac-m1_mini_2020-perf', 'mac', 'mac-arm-builder-perf', target_arch='arm')
_AddIsolatedTestSpec(
    'mac-m1_mini_2020-perf-pgo',
    'mac',
    'mac-arm-builder-perf-pgo',
    target_arch='arm')

_AddIsolatedTestSpec('linux-perf', 'linux', 'linux-builder-perf')

_AddIsolatedTestSpec(
    'lacros-eve-perf',
    'chromeos',
    'chromeos-amd64-generic-lacros-builder-perf',
    target_bits=64,
    target_arch='intel',
    cros_boards='amd64-generic:eve:octopus')

_AddIsolatedTestSpec(
    'lacros-x86-perf',
    'chromeos',
    'chromeos-amd64-generic-lacros-builder-perf',
    target_bits=64,
    target_arch='intel',
    cros_boards='amd64-generic:eve:octopus')

# Deprecated in perf waterfall. Needed for pinpoint when running Chrome
# Health on old commits.
_AddPinpointTestSpec('mac-10_12_laptop_low_end-perf', 'mac', 'mac-builder-perf')

# Pinpoint-only bots
# android
_AddPinpointTestSpec(
    'android-go-perf-pgo',
    'android',
    'android-builder-perf-pgo',
    target_bits=32)
_AddPinpointTestSpec('android-pixel2-perf-pgo', 'android',
                     'android_arm64-builder-perf-pgo')
_AddPinpointTestSpec('android-pixel2_webview-perf-pgo', 'android',
                     'android_arm64-builder-perf-pgo')
_AddPinpointTestSpec('android-pixel4-perf-pgo', 'android',
                     'android_arm64-builder-perf-pgo')
_AddPinpointTestSpec('android-pixel4a_power-perf-pgo', 'android',
                     'android_arm64-builder-perf-pgo')
_AddPinpointTestSpec('android-pixel6-perf-pgo', 'android',
                     'android_arm64_high_end-builder-perf-pgo')
_AddPinpointTestSpec('android-pixel6-pro-perf-pgo', 'android',
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
_AddPinpointTestSpec('mac-laptop_low_end-perf-pgo', 'mac',
                     'mac-builder-perf-pgo')
_AddPinpointTestSpec('mac-laptop_high_end-perf-pgo', 'mac',
                     'mac-builder-perf-pgo')
_AddPinpointTestSpec(
    'mac-m1_mini_2020-perf-pgo',
    'mac',
    'mac-arm-builder-perf-pgo',
    target_arch='arm')
_AddPinpointTestSpec(
    'mac-14-m1-pro-perf', 'mac', 'mac-arm-builder-perf', target_arch='arm')
# windows
_AddPinpointTestSpec('win-10-perf-pgo', 'win', 'win64-builder-perf-pgo')
_AddPinpointTestSpec('win-10_laptop_low_end-perf-pgo', 'win',
                     'win64-builder-perf-pgo')
_AddPinpointTestSpec('win-10_amd_laptop-perf-pgo', 'win',
                     'win64-builder-perf-pgo')
