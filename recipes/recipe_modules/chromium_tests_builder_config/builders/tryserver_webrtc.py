# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from .. import builder_spec


SPEC = {
  'android_chromium_compile': builder_spec.BuilderSpec.create(
    android_config='base_config',
    chromium_apply_config=['dcheck', 'mb', 'android'],
    chromium_config='base_config',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Release',
      'TARGET_ARCH': 'arm',
      'TARGET_BITS': 64,
      'TARGET_PLATFORM': 'android',
    },
    gclient_apply_config=['android'],
    gclient_config='chromium',
    simulation_platform='linux',
  ),
  'linux_chromium_compile': builder_spec.BuilderSpec.create(
    chromium_apply_config=['dcheck', 'mb'],
    chromium_config='chromium',
    chromium_config_kwargs={'BUILD_CONFIG': 'Release', 'TARGET_BITS': 64},
    gclient_apply_config=[],
    gclient_config='chromium',
    simulation_platform='linux',
  ),
  'linux_chromium_compile_dbg': builder_spec.BuilderSpec.create(
    chromium_apply_config=['dcheck', 'mb'],
    chromium_config='chromium',
    chromium_config_kwargs={'BUILD_CONFIG': 'Release', 'TARGET_BITS': 64},
    gclient_apply_config=[],
    gclient_config='chromium',
    simulation_platform='linux',
  ),
  'mac_chromium_compile': builder_spec.BuilderSpec.create(
    chromium_apply_config=['dcheck', 'mb'],
    chromium_config='chromium',
    chromium_config_kwargs={'BUILD_CONFIG': 'Release', 'TARGET_BITS': 64},
    gclient_apply_config=[],
    gclient_config='chromium',
    simulation_platform='mac',
  ),
  'webrtc_linux_chromium': builder_spec.BuilderSpec.create(
    chromium_apply_config=['dcheck', 'mb'],
    chromium_config='chromium',
    chromium_config_kwargs={'BUILD_CONFIG': 'Release', 'TARGET_BITS': 64},
    gclient_apply_config=[],
    gclient_config='chromium',
    simulation_platform='linux',
  ),
  'win_chromium_compile': builder_spec.BuilderSpec.create(
    chromium_apply_config=['dcheck', 'mb'],
    chromium_config='chromium',
    chromium_config_kwargs={'BUILD_CONFIG': 'Release', 'TARGET_BITS': 64},
    gclient_apply_config=[],
    gclient_config='chromium',
    simulation_platform='win',
  ),
  'win_chromium_compile_dbg': builder_spec.BuilderSpec.create(
    chromium_apply_config=['dcheck', 'mb'],
    chromium_config='chromium',
    chromium_config_kwargs={'BUILD_CONFIG': 'Release', 'TARGET_BITS': 64},
    gclient_apply_config=[],
    gclient_config='chromium',
    simulation_platform='win',
  ),
}
