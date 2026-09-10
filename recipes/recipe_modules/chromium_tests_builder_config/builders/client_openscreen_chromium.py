# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from .. import builder_spec


SPEC = {
  'chromium_linux_x64': builder_spec.BuilderSpec.create(
    chromium_config='chromium',
    chromium_apply_config=['mb'],
    gclient_config='chromium',
    gclient_apply_config=['openscreen_tot'],
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Debug',
      'TARGET_BITS': 64,
    },
    simulation_platform='linux',
  ),
  'chromium_mac_arm64': builder_spec.BuilderSpec.create(
    chromium_config='chromium',
    chromium_apply_config=['mb'],
    gclient_config='chromium',
    gclient_apply_config=['openscreen_tot'],
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Debug',
      'TARGET_BITS': 64,
    },
    simulation_platform='mac',
  ),
  'chromium_win_x64': builder_spec.BuilderSpec.create(
    chromium_config='chromium',
    chromium_apply_config=['mb'],
    gclient_config='chromium',
    gclient_apply_config=['openscreen_tot'],
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Debug',
      'TARGET_BITS': 64,
    },
    simulation_platform='win',
  ),
}
