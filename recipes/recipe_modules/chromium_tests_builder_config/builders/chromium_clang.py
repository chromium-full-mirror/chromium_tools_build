# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from .. import builder_spec

RESULTS_URL = 'https://chromeperf.appspot.com'


def _chromium_clang_spec(**kwargs):
  return builder_spec.BuilderSpec.create(
      build_gs_bucket='chromium-clang-archive', **kwargs)


def config(name,
           android_config=None,
           build_config='Release',
           chromium_config='clang_tot_linux',
           target_arch='intel',
           target_bits=64,
           official=False):
  cfg = {
      'chromium_config': chromium_config,
      'chromium_apply_config': ['mb'],
      'gclient_config': 'chromium',
      'gclient_apply_config': ['clang_tot'],
      'chromium_config_kwargs': {
          'BUILD_CONFIG': build_config,
          'TARGET_ARCH': target_arch,
          'TARGET_BITS': target_bits,
      },
      'execution_mode': builder_spec.COMPILE_AND_TEST,
      'simulation_platform': 'linux',
  }

  if android_config:
    cfg['android_config'] = android_config
    cfg['chromium_config_kwargs']['TARGET_PLATFORM'] = 'android'
    cfg['gclient_apply_config'].append('android')

  if official:
    cfg['gclient_apply_config'].append('chrome_internal')
    cfg['gclient_apply_config'].append('checkout_pgo_profiles')

  return name, _chromium_clang_spec(**cfg)


SPEC = {
    'CFI Linux ToT':
        _chromium_clang_spec(
            chromium_config='clang_tot_linux',
            chromium_apply_config=['mb'],
            gclient_config='chromium',
            gclient_apply_config=['clang_tot'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            },
            simulation_platform='linux',
        ),
    'CFI Linux CF':
        _chromium_clang_spec(
            chromium_config='chromium',
            chromium_apply_config=['mb'],
            gclient_config='chromium',
            # Not a ToT bot so no clang_tot gclient_apply_config.
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            },
            cf_archive_build=True,
            cf_gs_bucket='chromium-browser-cfi',
            cf_gs_acl='public-read',
            cf_archive_name='cfi',
            simulation_platform='linux',
        ),
    'linux-win_cross-rel':
        _chromium_clang_spec(
            chromium_config='chromium_win_clang_tot',
            chromium_apply_config=['mb'],
            gclient_config='chromium',
            gclient_apply_config=['clang_tot', 'win'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
                'TARGET_PLATFORM': 'win',
            },
            simulation_platform='win',
        ),
    'ToTiOS':
        _chromium_clang_spec(
            chromium_config='clang_tot_ios',
            chromium_apply_config=[
                'mb',
                'mac_toolchain',
            ],
            gclient_config='ios',
            gclient_apply_config=['clang_tot'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
                'TARGET_PLATFORM': 'ios',
            },
            simulation_platform='mac',
        ),
    'ToTiOSDevice':
        _chromium_clang_spec(
            chromium_config='clang_tot_ios',
            chromium_apply_config=[
                'mb',
                'mac_toolchain',
            ],
            gclient_config='ios',
            gclient_apply_config=['clang_tot'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
                'TARGET_PLATFORM': 'ios',
            },
            simulation_platform='mac',
        ),
    'ToTWinCFI':
        _chromium_clang_spec(
            chromium_config='chromium_win_clang_tot',
            chromium_apply_config=['mb'],
            gclient_config='chromium',
            gclient_apply_config=['clang_tot'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 32,
            },
            simulation_platform='win',
        ),
    'ToTWinCFI64':
        _chromium_clang_spec(
            chromium_config='chromium_win_clang_tot',
            chromium_apply_config=['mb'],
            gclient_config='chromium',
            gclient_apply_config=['clang_tot'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            },
            simulation_platform='win',
        ),
    'ToTWinOfficial':
        _chromium_clang_spec(
            chromium_config='chromium_win_clang_tot',
            chromium_apply_config=['mb'],
            gclient_config='chromium',
            gclient_apply_config=[
                'clang_tot', 'chrome_internal', 'checkout_pgo_profiles'
            ],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 32,
            },
            simulation_platform='win',
        ),
    'ToTWinOfficial64':
        _chromium_clang_spec(
            chromium_config='chromium_win_clang_tot',
            chromium_apply_config=['mb'],
            gclient_config='chromium',
            gclient_apply_config=[
                'clang_tot', 'chrome_internal', 'checkout_pgo_profiles'
            ],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            },
            simulation_platform='win',
        ),
    'CrWinAsan':
        _chromium_clang_spec(
            chromium_config='chromium_win_clang_asan_tot',
            chromium_apply_config=['mb'],
            gclient_config='chromium',
            gclient_apply_config=['clang_tot'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 32,
            },
            simulation_platform='win',
        ),
    'CrWinAsan(dll)':
        _chromium_clang_spec(
            chromium_config='chromium_win_clang_asan_tot',
            chromium_apply_config=['mb'],
            gclient_config='chromium',
            gclient_apply_config=['clang_tot'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 32,
            },
            simulation_platform='win',
        ),
    'ToTChromeOS':
        _chromium_clang_spec(
            chromium_config='clang_tot_chromeos',
            chromium_apply_config=['mb'],
            gclient_config='chromium',
            gclient_apply_config=['clang_tot', 'chromeos'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
                'TARGET_PLATFORM': 'chromeos',
            },
            simulation_platform='linux',
        ),
    'ToTChromeOS (dbg)':
        _chromium_clang_spec(
            chromium_config='clang_tot_chromeos',
            chromium_apply_config=['mb'],
            gclient_config='chromium',
            gclient_apply_config=['clang_tot', 'chromeos'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Debug',
                'TARGET_BITS': 64,
                'TARGET_PLATFORM': 'chromeos',
            },
            simulation_platform='linux',
        ),
    'ToTFuchsia x64':
        _chromium_clang_spec(
            chromium_config='clang_tot_fuchsia',
            chromium_apply_config=['mb'],
            gclient_config='chromium',
            gclient_apply_config=[
                'clang_tot',
                'fuchsia_x64',
                'fuchsia_no_hooks',
            ],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
                'TARGET_PLATFORM': 'fuchsia',
            },
            # Serialize the tests to limit capacity usage.
            serialize_tests=True,
            simulation_platform='linux',
        ),
    'ToTFuchsiaOfficial arm64':
        _chromium_clang_spec(
            chromium_config='clang_tot_fuchsia',
            chromium_apply_config=['mb'],
            gclient_config='chromium',
            gclient_apply_config=[
                'checkout_pgo_profiles',
                'clang_tot',
                'fuchsia_arm64',
                'fuchsia_arm64_host',
                'fuchsia_no_hooks',
            ],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
                'TARGET_PLATFORM': 'fuchsia',
            },
            # Serialize the tests to limit capacity usage.
            serialize_tests=True,
            simulation_platform='linux',
        ),
    'ToTMac':
        _chromium_clang_spec(
            chromium_config='clang_tot_mac',
            chromium_apply_config=['mb'],
            gclient_config='chromium',
            gclient_apply_config=['clang_tot'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            },
            simulation_platform='mac',
        ),
    'ToTMacOfficial':
        _chromium_clang_spec(
            chromium_config='clang_tot_mac',
            chromium_apply_config=['mb'],
            gclient_config='chromium',
            gclient_apply_config=[
                'clang_tot', 'chrome_internal', 'checkout_pgo_profiles'
            ],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            },
            simulation_platform='mac',
        ),
    'ToTMac (dbg)':
        _chromium_clang_spec(
            chromium_config='clang_tot_mac',
            chromium_apply_config=['mb'],
            gclient_config='chromium',
            gclient_apply_config=['clang_tot'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Debug',
                'TARGET_BITS': 64,
            },
            simulation_platform='mac',
        ),
    'ToTMacASan':
        _chromium_clang_spec(
            chromium_config='clang_tot_mac_asan',
            chromium_apply_config=['mb'],
            gclient_config='chromium',
            gclient_apply_config=['clang_tot'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            },
            simulation_platform='mac',
        ),
    'ToTWin':
        _chromium_clang_spec(
            chromium_config='chromium_win_clang_tot',
            chromium_apply_config=['mb'],
            gclient_config='chromium',
            gclient_apply_config=['clang_tot'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 32,
            },
            simulation_platform='win',
        ),
    'ToTWin(dbg)':
        _chromium_clang_spec(
            chromium_config='chromium_win_clang_tot',
            chromium_apply_config=['mb'],
            gclient_config='chromium',
            gclient_apply_config=['clang_tot'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Debug',
                'TARGET_BITS': 32,
            },
            simulation_platform='win',
        ),
    'ToTWin(dll)':
        _chromium_clang_spec(
            chromium_config='chromium_win_clang_tot',
            chromium_apply_config=['mb'],
            gclient_config='chromium',
            gclient_apply_config=['clang_tot'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 32,
            },
            simulation_platform='win',
        ),
    'ToTWin64':
        _chromium_clang_spec(
            chromium_config='chromium_win_clang_tot',
            chromium_apply_config=['mb'],
            gclient_config='chromium',
            gclient_apply_config=['clang_tot'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            },
            simulation_platform='win',
        ),
    'ToTWinASanLibfuzzer':
        _chromium_clang_spec(
            chromium_config='chromium_win_clang_asan_tot',
            chromium_apply_config=['mb'],
            gclient_config='chromium',
            gclient_apply_config=['clang_tot'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            },
            simulation_platform='win',
        ),
    'ToTWin64(dbg)':
        _chromium_clang_spec(
            chromium_config='chromium_win_clang_tot',
            chromium_apply_config=['mb'],
            gclient_config='chromium',
            gclient_apply_config=['clang_tot'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Debug',
                'TARGET_BITS': 64,
            },
            simulation_platform='win',
        ),
    'ToTWin64(dll)':
        _chromium_clang_spec(
            chromium_config='chromium_win_clang_tot',
            chromium_apply_config=['mb'],
            gclient_config='chromium',
            gclient_apply_config=['clang_tot'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            },
            simulation_platform='win',
        ),
    'ToTWin64PGO':
        _chromium_clang_spec(
            chromium_config='chromium_win_clang_tot',
            chromium_apply_config=['mb'],
            gclient_config='chromium',
            gclient_apply_config=['clang_tot'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            },
            simulation_platform='win',
        ),
}

SPEC.update([
    config(
        'ToTAndroid',
        android_config='clang_builder_mb_x64',
        chromium_config='clang_tot_android',
        target_arch='arm',
        target_bits=32),
    config(
        'ToTAndroid (dbg)',
        android_config='clang_builder_mb_x64',
        chromium_config='clang_tot_android',
        target_arch='arm',
        target_bits=32,
        build_config='Debug'),
    config(
        'ToTAndroid64',
        android_config='clang_builder_mb_x64',
        chromium_config='clang_tot_android',
        target_arch='arm',
        target_bits=64),
    config(
        'ToTAndroid x86',
        android_config='clang_builder_mb_x64',
        chromium_config='clang_tot_android',
        target_bits=32),
    config(
        'ToTAndroid x64',
        android_config='clang_builder_mb_x64',
        chromium_config='clang_tot_android',
        target_bits=64),
    config(
        'ToTAndroidASan',
        android_config='asan_symbolize',
        chromium_config='clang_tot_android_asan',
        target_arch='arm',
        target_bits=32),
    config(
        'ToTAndroidOfficial',
        android_config='clang_builder_mb_x64',
        chromium_config='clang_tot_android',
        target_arch='arm',
        target_bits=32),
    config(
        'ToTAndroidCoverage x86',
        android_config='clang_builder_mb_x64',
        chromium_config='clang_tot_android',
        target_bits=32),
    config('ToTLinux'),
    config('ToTLinuxOfficial', official=True),
    config('ToTLinux (dbg)', build_config='Debug'),
    config('ToTLinuxASan', chromium_config='clang_tot_linux_asan'),
    config('ToTLinuxASanLibfuzzer', chromium_config='clang_tot_linux_asan'),
    config('ToTLinuxMSan'),
    config('ToTLinuxPGO'),
    config('ToTLinuxTSan'),
    config('ToTLinuxUBSanVptr', chromium_config='clang_tot_linux_ubsan_vptr'),
])
