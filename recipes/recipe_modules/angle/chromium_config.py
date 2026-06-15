# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from RECIPE_MODULES.build.chromium import CONFIG_CTX


@CONFIG_CTX(includes=['ninja', 'mb'])
def angle_base(c):
  c.project_generator.isolate_map_paths = [
      'infra/specs/gn_isolate_map.pyl',
  ]
  c.project_generator.config_path = 'infra/specs/angle_mb_config.pyl'
  c.targets_spec_dir = 'infra/specs'
  c.analyze_config_path = 'infra/specs/trybot_analyze_config.json'

  if c.HOST_PLATFORM == 'mac' and c.TARGET_PLATFORM != 'ios':
    # Update via recipe logic in api.chromium.runhooks and mac_toolchains DEPS
    # hook.
    c.mac_toolchain.enabled = False

  if c.TARGET_PLATFORM == 'mac':
    c.env.FORCE_MAC_TOOLCHAIN = 1


@CONFIG_CTX(includes=['angle_base', 'clang'])
def angle_clang(c):
  pass


@CONFIG_CTX(includes=['angle_base', 'gcc'])
def angle_non_clang(c):
  pass
