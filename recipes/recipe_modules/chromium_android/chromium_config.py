# Copyright 2013 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.config_types import Path
from recipe_engine import config as recipe_config

from RECIPE_MODULES.build.chromium import CONFIG_CTX

@CONFIG_CTX(includes=['android_common', 'ninja'],
            config_vars={'TARGET_ARCH': 'arm', 'TARGET_BITS': 32,
                         'TARGET_PLATFORM': 'android', 'BUILD_CONFIG': 'Debug'})
def base_config(c):
  c.compile_py.default_targets=[]

  if c.HOST_PLATFORM != 'linux':  # pragma: no cover
    raise recipe_config.BadConf('Can only build android on linux.')

@CONFIG_CTX(includes=['base_config', 'default_compiler', 'goma'])
def main_builder(c):
  if c.TARGET_ARCH != 'arm':  # pragma: no cover
    raise recipe_config.BadConf(
      'Cannot target arm with TARGET_ARCH == %s' % c.TARGET_ARCH)

@CONFIG_CTX(includes=['main_builder', 'mb'])
def main_builder_mb(_):
  pass

@CONFIG_CTX(includes=['main_builder_mb'],
            config_vars={'BUILD_CONFIG': 'Release'})
def main_builder_rel_mb(_):
  pass

@CONFIG_CTX(includes=['base_config', 'clang', 'goma'])
def clang_builder(c):
  c.runtests.enable_asan = True

@CONFIG_CTX(includes=['clang_builder', 'mb'])
def clang_builder_mb(_):
  pass


@CONFIG_CTX(
    includes=['base_config', 'default_compiler', 'goma'],
    config_vars={
        'TARGET_ARCH': 'riscv64',
        'TARGET_BITS': 64
    })
def riscv64_builder(c):
  if c.TARGET_ARCH != 'riscv64':  # pragma: no cover
    raise recipe_config.BadConf('Cannot target riscv64 with TARGET_ARCH == %s' %
                                c.TARGET_ARCH)


@CONFIG_CTX(includes=['riscv64_builder', 'mb'])
def riscv64_builder_mb(_):
  pass


@CONFIG_CTX(includes=['base_config', 'default_compiler', 'goma'],
            config_vars={'TARGET_ARCH': 'intel'})
def x86_builder(c):
  if c.TARGET_ARCH != 'intel':  # pragma: no cover
    raise recipe_config.BadConf(
      'Cannot target x86 with TARGET_ARCH == %s' % c.TARGET_ARCH)

@CONFIG_CTX(includes=['x86_builder', 'mb'])
def x86_builder_mb(_):
  pass

@CONFIG_CTX(includes=['base_config', 'default_compiler', 'goma'],
            config_vars={'TARGET_ARCH': 'mipsel'})
def mipsel_builder(c):
  if c.TARGET_ARCH != 'mipsel':  # pragma: no cover
    raise recipe_config.BadConf('I dunno what to put in a mips builder!')

@CONFIG_CTX(includes=['mipsel_builder', 'mb'])
def mipsel_builder_mb(_):
  pass

@CONFIG_CTX(includes=['clobber'])
def cronet_builder(c):
  # From //tools/mb/mb_config.pyl's "cronet_common":
  c.gn_args.append('is_cronet_build=true')
  c.gn_args.append('disable_file_support=true')
  c.gn_args.append('enable_websockets=false')
  c.gn_args.append('include_transport_security_state_preload_list=false')
  c.gn_args.append('use_crash_key_stubs=true')
  c.gn_args.append('use_platform_icu_alternatives=true')

  # From //tools/mb/mb_config.pyl's "cronet_android":
  c.gn_args.append('use_partition_alloc=false')
  c.gn_args.append('enable_reporting=true')
  c.gn_args.append('use_hashed_jni_names=true')
  c.gn_args.append('default_min_sdk_version=21')
  c.gn_args.append('clang_use_default_sample_profile=false')
  c.gn_args.append('media_use_ffmpeg=false')
  c.gn_args.append('use_thin_lto=false')
  c.gn_args.append('enable_resource_allowlist_generation=false')
  c.gn_args.append('enable_jdk_library_desugaring=false')

  c.compile_py.default_targets=[
      'cronet_package',
      'cronet_perf_test_apk',
      'cronet_sample_test_apk',
      'cronet_smoketests_missing_native_library_instrumentation_apk',
      'cronet_smoketests_platform_only_instrumentation_apk',
      'cronet_test_instrumentation_apk',
      'cronet_unittests_android',
      'net_unittests']

@CONFIG_CTX()
def cronet_official(c):
  c.gn_args.append('is_official_build=true')

@CONFIG_CTX(includes=['main_builder'],
            config_vars={'BUILD_CONFIG': 'Release'})
def arm_v6_builder_rel(c):  # pragma: no cover
  c.gn_args.append('arm_version=6')

@CONFIG_CTX(includes=['main_builder'])
def arm_l_builder(_):  # pragma: no cover
  pass

@CONFIG_CTX(includes=['arm_l_builder'])
def arm_l_builder_lto(c):  # pragma: no cover
  del c

@CONFIG_CTX(includes=['arm_l_builder'],
            config_vars={'BUILD_CONFIG': 'Release'})
def arm_l_builder_rel(_):  # pragma: no cover
  pass

@CONFIG_CTX(includes=['base_config', 'default_compiler', 'goma'],
            config_vars={'TARGET_ARCH': 'intel', 'TARGET_BITS': 64})
def x64_builder(c):
  if c.TARGET_ARCH != 'intel' or c.TARGET_BITS != 64:
    raise recipe_config.BadConf(
      'Cannot target x64 with TARGET_ARCH == %s, TARGET_BITS == %d'
       % (c.TARGET_ARCH, c.TARGET_BITS))  # pragma: no cover

@CONFIG_CTX(includes=['x64_builder', 'mb'])
def x64_builder_mb(_):
  pass

@CONFIG_CTX(includes=['base_config', 'default_compiler', 'goma'],
            config_vars={'TARGET_BITS': 64})
def arm64_builder(_):
  pass

@CONFIG_CTX(includes=['arm64_builder', 'mb'])
def arm64_builder_mb(_):
  pass

@CONFIG_CTX(includes=['arm64_builder'],
            config_vars={'BUILD_CONFIG': 'Release'})
def arm64_builder_rel(_):  # pragma: no cover
  pass

@CONFIG_CTX(includes=['arm64_builder_rel', 'mb'])
def arm64_builder_rel_mb(_):
  pass

@CONFIG_CTX(includes=['main_builder'])
def try_builder(_):
  pass  # pragma: no cover

@CONFIG_CTX(includes=['x86_builder'])
def x86_try_builder(_):
  pass  # pragma: no cover

@CONFIG_CTX(includes=['base_config'])
def tests_base(_):
  pass  # pragma: no cover

@CONFIG_CTX(includes=['arm64_builder_rel'])
def tests_arm64(_):  # pragma: no cover
  pass

@CONFIG_CTX(includes=['tests_base'])
def main_tests(_):
  pass  # pragma: no cover

@CONFIG_CTX(includes=['tests_base'])
def clang_tests(_):
  pass  # pragma: no cover

@CONFIG_CTX(includes=['tests_base'])
def enormous_tests(_):
  pass  # pragma: no cover

@CONFIG_CTX(includes=['tests_base'])
def try_instrumentation_tests(_):
  pass  # pragma: no cover

@CONFIG_CTX(includes=['x86_builder'])
def x86_try_instrumentation_tests(_):
  pass  # pragma: no cover

@CONFIG_CTX(includes=['main_builder'],
            config_vars={'BUILD_CONFIG': 'Debug'})
def coverage_builder_tests(c):  # pragma: no cover
  del c

@CONFIG_CTX(includes=['main_builder'])
def incremental_coverage_builder_tests(c):
  del c

@CONFIG_CTX(includes=['main_builder'])
def non_device_wipe_provisioning(_):
  pass

@CONFIG_CTX(includes=['main_builder'])
def cast_builder(c):
  del c

@CONFIG_CTX()
def disable_neon(c):  # pragma: no cover
  c.gn_args.append('arm_use_neon=false')

@CONFIG_CTX()
def errorprone(c):
  del c
