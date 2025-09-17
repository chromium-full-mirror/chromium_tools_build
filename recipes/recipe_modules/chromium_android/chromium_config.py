# Copyright 2013 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import config as recipe_config

from RECIPE_MODULES.build.chromium import CONFIG_CTX


@CONFIG_CTX(includes=['android_common', 'ninja'])
def base_config(c):
  c.compile_py.default_targets=[]

  if c.HOST_PLATFORM != 'linux':  # pragma: no cover
    raise recipe_config.BadConf('Can only build android on linux.')


@CONFIG_CTX(includes=['base_config', 'default_compiler'])
def main_builder(c):
  pass


@CONFIG_CTX(includes=['base_config', 'clang'])
def clang_builder(c):
  c.runtests.enable_asan = True


@CONFIG_CTX()
def cronet_builder(c):
  # From //tools/mb/mb_config.pyl's "cronet_common":
  c.gn_args.append('is_cronet_build=true')
  c.gn_args.append('enable_websockets=false')
  c.gn_args.append('include_transport_security_state_preload_list=false')
  c.gn_args.append('use_platform_icu_alternatives=true')

  # From //tools/mb/mb_config.pyl's "cronet_android":
  c.gn_args.append('use_partition_alloc=false')
  c.gn_args.append('use_hashed_jni_names=true')
  c.gn_args.append('default_min_sdk_version=23')
  c.gn_args.append('clang_use_default_sample_profile=false')
  c.gn_args.append('enable_resource_allowlist_generation=false')

  c.compile_py.default_targets=[
      'cronet_package',
      'cronet_sample_test_apk',
      'cronet_smoketests_missing_native_library_instrumentation_apk',
      'cronet_smoketests_platform_only_instrumentation_apk',
      'cronet_test_instrumentation_apk',
      'cronet_unittests_android',
      'net_unittests']


@CONFIG_CTX(includes=['clobber'])
def cronet_official(c):
  c.gn_args.append('is_official_build=true')


@CONFIG_CTX()
def disable_neon(c):  # pragma: no cover
  c.gn_args.append('arm_use_neon=false')
