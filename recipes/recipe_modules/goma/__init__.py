# Copyright 2015 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

DEPS = [
    'builder_group',
    'depot_tools/depot_tools',
    'depot_tools/gclient',
    'puppet_service_account',
    'recipe_engine/buildbucket',
    'recipe_engine/cipd',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/futures',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/runtime',
    'recipe_engine/step',
    'recipe_engine/swarming',
    'recipe_engine/time',
]

from recipe_engine.recipe_api import Property
from recipe_engine.config import ConfigGroup, Single


PROPERTIES = {
  '$build/goma': Property(
    help='Properties specifically for the goma module',
    param_name='properties',
    kind=ConfigGroup(
      # How many jobs to run in parallel.
      # Allow floats because of crbug.com/914996.
      jobs=Single((int, float)),
      # Whether or not to turn on debug mode.
      debug=Single(bool),
      # Whether or not we're running locally and should pick the local client
      # from this path.
      local=Single(str),
      # Whether to enable Arbitrary Toolchain Support in Goma client.
      enable_ats=Single(bool),
      # Explicitly specify a URL for a Goma server.
      server_host=Single(str),
      # Extra parameters to append to RPC path in `server_host`.
      rpc_extra_params=Single(str),
      # Whether to use ambient luci auth rather than puppet-provided
      # credentials.
      use_luci_auth=Single(bool),
    ),
    default={},
  ),
}
