# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from .. import builder_spec

COMMON_BOT_CONFIG = builder_spec.BuilderSpec.create(
  chromium_config='chromium',
  chromium_apply_config=['mb'],
  gclient_config='chromium',
  chromium_config_kwargs={
    'BUILD_CONFIG': 'Release',
    'TARGET_BITS': 64,
  },
  simulation_platform='linux',
)

SPEC = {
  'devtools_frontend_linux_blink_light_rel': COMMON_BOT_CONFIG,
  'devtools_frontend_linux_blink_rel': COMMON_BOT_CONFIG,
}
