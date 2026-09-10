# Copyright 2013 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from RECIPE_MODULES.build.chromium import CONFIG_CTX


@CONFIG_CTX(includes=['ninja'])
def v8(c):
  c.project_generator.tool = 'mb'
  c.project_generator.isolate_map_paths = [
    'infra/mb/gn_isolate_map.pyl',
  ]
  c.build_config_fs = 'build'

  if c.HOST_PLATFORM == 'mac' and c.TARGET_PLATFORM != 'ios':
    # Update via recipe logic in api.chromium.runhooks and mac_toolchains DEPS
    # hook.
    c.mac_toolchain.enabled = False

  if c.TARGET_PLATFORM == 'mac':
    c.env.FORCE_MAC_TOOLCHAIN = 1

  # Opt out of using gyp environment variables.
  c.use_gyp_env = False


@CONFIG_CTX(includes=['v8'])
def arm_hard_float(c):
  c.gn_args.append('arm_float_abi="hard"')


@CONFIG_CTX(includes=['v8'])
def default_target_v8_clusterfuzz(c):
  c.compile_py.default_targets = ['v8_clusterfuzz']


@CONFIG_CTX(includes=['v8'])
def default_target_d8(c):
  c.compile_py.default_targets = ['d8']


@CONFIG_CTX(includes=['v8'])
def default_target_v8_archive(c):
  c.compile_py.default_targets = ['v8_archive']


@CONFIG_CTX(includes=['v8'])
def v8_android(c):
  c.gn_args.append('symbol_level=1')
  c.gn_args.append('v8_android_log_stdout=true')
  c.gn_args.append('android_unstripped_runtime_outputs=false')
  c.gn_args.append('default_min_sdk_version=23')


@CONFIG_CTX(includes=['v8'])
def v8_static_library(c):
  c.gn_args.append('v8_static_library=true')


@CONFIG_CTX(includes=['v8'])
def slow_dchecks(c):
  c.gn_args.append('v8_enable_slow_dchecks=true')


@CONFIG_CTX(includes=['ninja', 'gn', 'clang'])
def node_ci(c):
  c.use_gyp_env = False
  c.gn_args.append('node_enable_deprecated_declarations_warnings=true')
  c.gn_args.append('v8_imminent_deprecation_warnings=false')
  c.gn_args.append('v8_array_buffer_internal_field_count=2')
  c.gn_args.append('v8_array_buffer_view_internal_field_count=2')
  if c.HOST_PLATFORM != 'win':
    c.gn_args.append('use_sysroot=true')
    c.gn_args.append('use_custom_libcxx=true')
    c.gn_args.append('icu_use_data_file=false')


@CONFIG_CTX(includes=['node_ci'])
def node_ci_debug(c):
  c.gn_args.append('is_component_build=true')
  c.gn_args.append('symbol_level=1')
  c.gn_args.append('v8_enable_backtrace=true')
  c.gn_args.append('v8_enable_fast_mksnapshot=true')
  c.gn_args.append('v8_enable_slow_dchecks=true')
