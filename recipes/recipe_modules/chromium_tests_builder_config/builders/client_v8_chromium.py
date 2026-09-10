# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from .. import builder_spec


def _client_v8_chromium_spec(**kwargs):
  return builder_spec.BuilderSpec.create(**kwargs)


SPEC = {
  'Linux V8 API Stability': _client_v8_chromium_spec(
    chromium_config='chromium',
    chromium_apply_config=['mb'],
    gclient_config='chromium',
    gclient_apply_config=['v8_canary', 'with_branch_heads'],
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Release',
      'TARGET_BITS': 64,
    },
    simulation_platform='linux',
  ),
}
