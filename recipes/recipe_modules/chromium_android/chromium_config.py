# Copyright 2013 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

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
  # DO NOT ADD GN ARGS HERE. Special snowflake gn args are a pain to maintain;
  # see https://crbug.com/40287068. Instead, change the GN arg declaration so
  # that the default value for the arg is derived from the `is_cronet_build`
  # GN arg.
  c.gn_args.append('is_cronet_build=true')
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
