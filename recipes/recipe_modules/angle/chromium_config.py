# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from RECIPE_MODULES.build.chromium import CONFIG_CTX


@CONFIG_CTX(includes=['ninja', 'mb'])
def angle_base(c):
  c.project_generator.isolate_map_paths = [
      c.CHECKOUT_PATH.join('infra', 'specs', 'gn_isolate_map.pyl'),
  ]
  c.project_generator.config_path = c.CHECKOUT_PATH.join(
      'infra', 'specs', 'angle_mb_config.pyl')
  c.build_dir = c.CHECKOUT_PATH.join('out')
  c.source_side_spec_dir = c.CHECKOUT_PATH.join('infra', 'specs')
  c.analyze_config_path = c.CHECKOUT_PATH.join('infra', 'specs',
                                               'trybot_analyze_config.json')

  if c.HOST_PLATFORM == 'mac' and c.TARGET_PLATFORM != 'ios':
    # Update via recipe logic in api.chromium.runhooks and mac_toolchains DEPS
    # hook.
    c.mac_toolchain.enabled = False

  if c.TARGET_PLATFORM == 'mac':
    c.env.FORCE_MAC_TOOLCHAIN = 1


@CONFIG_CTX(includes=['angle_base', 'clang', 'goma'])
def angle_clang(c):
  pass


@CONFIG_CTX(includes=['angle_base', 'gcc'])
def angle_non_clang(c):
  pass
