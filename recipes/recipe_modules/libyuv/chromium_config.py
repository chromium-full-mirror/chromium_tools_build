# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine.config import BadConf

from RECIPE_MODULES.build.chromium import CONFIG_CTX


@CONFIG_CTX(includes=['ninja', 'default_compiler'])
def libyuv(c):
  _libyuv_common(c)


@CONFIG_CTX(includes=['chromium_clang'])
def libyuv_clang(c):
  _libyuv_common(c)


@CONFIG_CTX(includes=['ninja', 'gcc'])
def libyuv_gcc(c):
  _libyuv_common(c)


@CONFIG_CTX(includes=['ninja'])
def libyuv_android(c):
  _libyuv_common(c)
  c.gn_args.append('android_static_analysis="off"')


@CONFIG_CTX(includes=['ninja'])
def libyuv_msvc(c):
  _libyuv_common(c)
  c.gn_args.append('is_clang=false')
  c.gn_args.append('use_lld=false')
  c.gn_args.append('use_custom_libcxx=false')
  c.gn_args.append('use_llvm_libatomic=false')


@CONFIG_CTX(includes=['chromium'])
def libyuv_ios(c):
  if c.HOST_PLATFORM != 'mac':
    raise BadConf(
      'Only "mac" host platform is supported for iOS (got: "%s")'
      % c.HOST_PLATFORM
    )  # pragma: no cover
  if c.TARGET_PLATFORM != 'ios':
    raise BadConf(
      'Only "ios" target platform is supported (got: "%s")' % c.TARGET_PLATFORM
    )  # pragma: no cover
  c.build_config_fs = c.BUILD_CONFIG + '-iphoneos'

  c.gn_args.append('ios_enable_code_signing=false')
  c.gn_args.append('target_environment="simulator"')
  c.gn_args.append('target_os="%s"' % c.TARGET_PLATFORM)
  _libyuv_common(c)


def _libyuv_common(c):
  c.compile_py.default_targets = []
  # TODO(https://bugs.chromium.org/p/libyuv/issues/detail?id=883): After
  # switching to absl/flags, component builds are not working correctly
  # see crbug.com/1166430 for a way to fix.
  _libyuv_static_build(c)


def _libyuv_static_build(c):
  # TODO(kjellander): Investigate moving this into chromium recipe module's
  # static_library config instead.
  if c.BUILD_CONFIG == 'Debug':
    # GN defaults to component builds for Debug, but some build configurations
    # (Android and iOS) needs it to be static.
    c.gn_args.append('is_component_build=false')
