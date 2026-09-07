# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from .. import builder_spec


def _client_devtools_frontend_integration_spec(**kwargs):
  return builder_spec.BuilderSpec.create(
      **kwargs)


_common_config = _client_devtools_frontend_integration_spec(
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
    'DevTools Linux': _common_config,
}
