# Copyright 2013 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from RECIPE_MODULES.build.chromium import CONFIG_CTX


@CONFIG_CTX(includes=['ninja', 'default_compiler', 'goma'])
def webrtc_default(c):
  c.targets_spec_dir = c.CHECKOUT_PATH.join('infra', 'specs')
  c.analyze_config_path = c.CHECKOUT_PATH.join('infra', 'specs',
                                               'trybot_analyze_config.json')
  c.compile_py.default_targets = []


# TODO(kjellander): Remove as soon there's a way to get the sanitizer bots to
# set swarming tags properly without the chromium recipe module configs (which
# depend on clang)
@CONFIG_CTX(includes=['ninja', 'clang', 'goma'])
def webrtc_clang(c):
  c.targets_spec_dir = c.CHECKOUT_PATH.join('infra', 'specs')
  c.analyze_config_path = c.CHECKOUT_PATH.join('infra', 'specs',
                                               'trybot_analyze_config.json')
  c.compile_py.default_targets = []


@CONFIG_CTX(includes=['android'])
def webrtc_android(c):
  c.targets_spec_dir = c.CHECKOUT_PATH.join('infra', 'specs')
  c.analyze_config_path = c.CHECKOUT_PATH.join('infra', 'specs',
                                               'trybot_analyze_config.json')
