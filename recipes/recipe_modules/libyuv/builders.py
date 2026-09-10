# Copyright 2016 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

# Contains the bulk of the libyuv builder configurations to improve readability
# of the recipe.

from __future__ import annotations

from RECIPE_MODULES.build.attr_utils import attrib, attrs
from RECIPE_MODULES.build.chromium_tests_builder_config import (
  builder_db,
  builder_spec,
)


@attrs()
class LibYUVBuilderSpec(builder_spec.BuilderSpec):
  bot_type = attrib(str, default=None)
  ensure_sdk = attrib(str, default=None)
  triggers = attrib(str, default=None)


_CLIENT_LIBYUV_SPEC = {
  'Win32 Debug': LibYUVBuilderSpec.create(
    chromium_config='libyuv_msvc',
    gclient_config='libyuv',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Debug',
      'TARGET_BITS': 32,
    },
    bot_type='builder_tester',
    simulation_platform='win',
  ),
  'Win32 Release': LibYUVBuilderSpec.create(
    chromium_config='libyuv_msvc',
    gclient_config='libyuv',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Release',
      'TARGET_BITS': 32,
    },
    bot_type='builder_tester',
    simulation_platform='win',
  ),
  'Win64 Debug': LibYUVBuilderSpec.create(
    chromium_config='libyuv_msvc',
    gclient_config='libyuv',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Debug',
      'TARGET_BITS': 64,
    },
    bot_type='builder_tester',
    simulation_platform='win',
  ),
  'Win64 Release': LibYUVBuilderSpec.create(
    chromium_config='libyuv_msvc',
    gclient_config='libyuv',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Release',
      'TARGET_BITS': 64,
    },
    bot_type='builder_tester',
    simulation_platform='win',
  ),
  'Win32 Debug (Clang)': LibYUVBuilderSpec.create(
    chromium_config='libyuv_clang',
    gclient_config='libyuv',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Debug',
      'TARGET_BITS': 32,
    },
    bot_type='builder_tester',
    simulation_platform='win',
  ),
  'Win32 Release (Clang)': LibYUVBuilderSpec.create(
    chromium_config='libyuv_clang',
    gclient_config='libyuv',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Release',
      'TARGET_BITS': 32,
    },
    bot_type='builder_tester',
    simulation_platform='win',
  ),
  'Win64 Debug (Clang)': LibYUVBuilderSpec.create(
    chromium_config='libyuv_clang',
    gclient_config='libyuv',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Debug',
      'TARGET_BITS': 64,
    },
    bot_type='builder_tester',
    simulation_platform='win',
  ),
  'Win64 Release (Clang)': LibYUVBuilderSpec.create(
    chromium_config='libyuv_clang',
    gclient_config='libyuv',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Release',
      'TARGET_BITS': 64,
    },
    bot_type='builder_tester',
    simulation_platform='win',
  ),
  'Mac64 Debug': LibYUVBuilderSpec.create(
    chromium_config='libyuv',
    gclient_config='libyuv',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Debug',
      'TARGET_BITS': 64,
    },
    bot_type='builder_tester',
    simulation_platform='mac',
  ),
  'Mac64 Release': LibYUVBuilderSpec.create(
    chromium_config='libyuv',
    gclient_config='libyuv',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Release',
      'TARGET_BITS': 64,
    },
    bot_type='builder_tester',
    simulation_platform='mac',
  ),
  'Mac Asan': LibYUVBuilderSpec.create(
    chromium_config='libyuv_clang',
    gclient_config='libyuv',
    chromium_apply_config=['asan'],
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Release',
      'TARGET_BITS': 64,
    },
    bot_type='builder_tester',
    simulation_platform='mac',
  ),
  'iOS Debug': LibYUVBuilderSpec.create(
    chromium_config='libyuv_ios',
    gclient_config='libyuv_ios',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Debug',
      'TARGET_BITS': 32,
      'TARGET_ARCH': 'arm',
      'TARGET_PLATFORM': 'ios',
    },
    bot_type='builder',
    simulation_platform='mac',
    ensure_sdk='ios',
  ),
  'iOS Release': LibYUVBuilderSpec.create(
    chromium_config='libyuv_ios',
    gclient_config='libyuv_ios',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Release',
      'TARGET_BITS': 32,
      'TARGET_ARCH': 'arm',
      'TARGET_PLATFORM': 'ios',
    },
    bot_type='builder',
    simulation_platform='mac',
    ensure_sdk='ios',
  ),
  'iOS ARM64 Debug': LibYUVBuilderSpec.create(
    chromium_config='libyuv_ios',
    gclient_config='libyuv_ios',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Debug',
      'TARGET_BITS': 64,
      'TARGET_ARCH': 'arm',
      'TARGET_PLATFORM': 'ios',
    },
    bot_type='builder',
    simulation_platform='mac',
    ensure_sdk='ios',
  ),
  'iOS ARM64 Release': LibYUVBuilderSpec.create(
    chromium_config='libyuv_ios',
    gclient_config='libyuv_ios',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Release',
      'TARGET_BITS': 64,
      'TARGET_ARCH': 'arm',
      'TARGET_PLATFORM': 'ios',
    },
    bot_type='builder',
    simulation_platform='mac',
    ensure_sdk='ios',
  ),
  'Linux32 Debug': LibYUVBuilderSpec.create(
    chromium_config='libyuv',
    gclient_config='libyuv',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Debug',
      'TARGET_BITS': 32,
    },
    bot_type='builder_tester',
    simulation_platform='linux',
  ),
  'Linux32 Release': LibYUVBuilderSpec.create(
    chromium_config='libyuv',
    gclient_config='libyuv',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Release',
      'TARGET_BITS': 32,
    },
    bot_type='builder_tester',
    simulation_platform='linux',
  ),
  'Linux64 Debug': LibYUVBuilderSpec.create(
    chromium_config='libyuv',
    gclient_config='libyuv',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Debug',
      'TARGET_BITS': 64,
    },
    bot_type='builder_tester',
    simulation_platform='linux',
  ),
  'Linux64 Release': LibYUVBuilderSpec.create(
    chromium_config='libyuv',
    gclient_config='libyuv',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Release',
      'TARGET_BITS': 64,
    },
    bot_type='builder_tester',
    simulation_platform='linux',
  ),
  'Linux GCC': LibYUVBuilderSpec.create(
    chromium_config='libyuv_gcc',
    gclient_config='libyuv',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Release',
      'TARGET_BITS': 64,
    },
    bot_type='builder_tester',
    simulation_platform='linux',
  ),
  'Linux Asan': LibYUVBuilderSpec.create(
    chromium_config='libyuv_clang',
    gclient_config='libyuv',
    chromium_apply_config=['asan', 'lsan'],
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Release',
      'TARGET_BITS': 64,
    },
    bot_type='builder_tester',
    simulation_platform='linux',
  ),
  'Linux MSan': LibYUVBuilderSpec.create(
    chromium_config='libyuv_clang',
    gclient_config='libyuv',
    chromium_apply_config=['msan'],
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Release',
      'TARGET_BITS': 64,
    },
    bot_type='builder_tester',
    simulation_platform='linux',
  ),
  'Linux Tsan v2': LibYUVBuilderSpec.create(
    chromium_config='libyuv_clang',
    gclient_config='libyuv',
    chromium_apply_config=['tsan2'],
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Release',
      'TARGET_BITS': 64,
    },
    bot_type='builder_tester',
    simulation_platform='linux',
  ),
  'Linux UBSan': LibYUVBuilderSpec.create(
    chromium_config='libyuv_clang',
    gclient_config='libyuv',
    chromium_apply_config=['ubsan'],
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Release',
      'TARGET_BITS': 64,
    },
    bot_type='builder_tester',
    simulation_platform='linux',
  ),
  'Linux UBSan vptr': LibYUVBuilderSpec.create(
    chromium_config='libyuv_clang',
    gclient_config='libyuv',
    chromium_apply_config=['ubsan_vptr'],
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Release',
      'TARGET_BITS': 64,
    },
    bot_type='builder_tester',
    simulation_platform='linux',
  ),
  'Android Debug': LibYUVBuilderSpec.create(
    chromium_config='libyuv_android',
    gclient_config='libyuv_android',
    android_config='libyuv',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Debug',
      'TARGET_PLATFORM': 'android',
      'TARGET_ARCH': 'arm',
      'TARGET_BITS': 32,
    },
    bot_type='builder',
    simulation_platform='linux',
    triggers='Android Tester ARM32 Debug (Nexus 5X)',
  ),
  'Android Release': LibYUVBuilderSpec.create(
    chromium_config='libyuv_android',
    gclient_config='libyuv_android',
    android_config='libyuv',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Release',
      'TARGET_PLATFORM': 'android',
      'TARGET_ARCH': 'arm',
      'TARGET_BITS': 32,
    },
    bot_type='builder',
    simulation_platform='linux',
    triggers='Android Tester ARM32 Release (Nexus 5X)',
  ),
  'Android ARM64 Debug': LibYUVBuilderSpec.create(
    chromium_config='libyuv_android',
    gclient_config='libyuv_android',
    android_config='libyuv',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Debug',
      'TARGET_PLATFORM': 'android',
      'TARGET_ARCH': 'arm',
      'TARGET_BITS': 64,
    },
    bot_type='builder',
    simulation_platform='linux',
    triggers='Android Tester ARM64 Debug (Nexus 5X)',
  ),
  'Android32 x86 Debug': LibYUVBuilderSpec.create(
    chromium_config='libyuv_android',
    gclient_config='libyuv_android',
    android_config='libyuv',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Debug',
      'TARGET_PLATFORM': 'android',
      'TARGET_ARCH': 'intel',
      'TARGET_BITS': 32,
    },
    bot_type='builder',
    simulation_platform='linux',
  ),
  'Android32 MIPS Debug': LibYUVBuilderSpec.create(
    chromium_config='libyuv_android',
    gclient_config='libyuv_android',
    android_config='libyuv',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Debug',
      'TARGET_PLATFORM': 'android',
      'TARGET_ARCH': 'mipsel',
      'TARGET_BITS': 32,
    },
    bot_type='builder',
    simulation_platform='linux',
  ),
  'Android64 x64 Debug': LibYUVBuilderSpec.create(
    chromium_config='libyuv_android',
    gclient_config='libyuv_android',
    android_config='libyuv',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Debug',
      'TARGET_PLATFORM': 'android',
      'TARGET_ARCH': 'intel',
      'TARGET_BITS': 64,
    },
    bot_type='builder',
    simulation_platform='linux',
  ),
  'Android Tester ARM32 Debug (Nexus 5X)': LibYUVBuilderSpec.create(
    chromium_config='libyuv_android',
    gclient_config='libyuv_android',
    android_config='libyuv',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Debug',
      'TARGET_PLATFORM': 'android',
      'TARGET_ARCH': 'arm',
      'TARGET_BITS': 32,
    },
    bot_type='tester',
    execution_mode=builder_spec.TEST,
    parent_builder_group='client.libyuv',
    parent_buildername='Android Debug',
    simulation_platform='linux',
  ),
  'Android Tester ARM32 Release (Nexus 5X)': LibYUVBuilderSpec.create(
    chromium_config='libyuv_android',
    gclient_config='libyuv_android',
    android_config='libyuv',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Release',
      'TARGET_PLATFORM': 'android',
      'TARGET_ARCH': 'arm',
      'TARGET_BITS': 32,
    },
    bot_type='tester',
    execution_mode=builder_spec.TEST,
    parent_builder_group='client.libyuv',
    parent_buildername='Android Release',
    simulation_platform='linux',
  ),
  'Android Tester ARM64 Debug (Nexus 5X)': LibYUVBuilderSpec.create(
    chromium_config='libyuv_android',
    gclient_config='libyuv_android',
    android_config='libyuv',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Debug',
      'TARGET_PLATFORM': 'android',
      'TARGET_ARCH': 'arm',
      'TARGET_BITS': 64,
    },
    bot_type='tester',
    execution_mode=builder_spec.TEST,
    parent_builder_group='client.libyuv',
    parent_buildername='Android ARM64 Debug',
    simulation_platform='linux',
  ),
}

_TRYSERVER_LIBYUV_SPEC = {
  'win': LibYUVBuilderSpec.create(
    chromium_config='libyuv_msvc',
    gclient_config='libyuv',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Debug',
      'TARGET_BITS': 32,
    },
    bot_type='builder_tester',
    simulation_platform='win',
  ),
  'win_rel': LibYUVBuilderSpec.create(
    chromium_config='libyuv_msvc',
    gclient_config='libyuv',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Release',
      'TARGET_BITS': 32,
    },
    bot_type='builder_tester',
    simulation_platform='win',
  ),
  'win_x64_rel': LibYUVBuilderSpec.create(
    chromium_config='libyuv_msvc',
    gclient_config='libyuv',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Release',
      'TARGET_BITS': 64,
    },
    bot_type='builder_tester',
    simulation_platform='win',
  ),
  'win_clang': LibYUVBuilderSpec.create(
    chromium_config='libyuv_clang',
    gclient_config='libyuv',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Debug',
      'TARGET_BITS': 32,
    },
    bot_type='builder_tester',
    simulation_platform='win',
  ),
  'win_clang_rel': LibYUVBuilderSpec.create(
    chromium_config='libyuv_clang',
    gclient_config='libyuv',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Release',
      'TARGET_BITS': 32,
    },
    bot_type='builder_tester',
    simulation_platform='win',
  ),
  'win_x64_clang_rel': LibYUVBuilderSpec.create(
    chromium_config='libyuv_clang',
    gclient_config='libyuv',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Release',
      'TARGET_BITS': 64,
    },
    bot_type='builder_tester',
    simulation_platform='win',
  ),
  'mac': LibYUVBuilderSpec.create(
    chromium_config='libyuv',
    gclient_config='libyuv',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Debug',
      'TARGET_BITS': 64,
    },
    bot_type='builder_tester',
    simulation_platform='mac',
  ),
  'mac_rel': LibYUVBuilderSpec.create(
    chromium_config='libyuv',
    gclient_config='libyuv',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Release',
      'TARGET_BITS': 64,
    },
    bot_type='builder_tester',
    simulation_platform='mac',
  ),
  'mac_asan': LibYUVBuilderSpec.create(
    chromium_config='libyuv_clang',
    gclient_config='libyuv',
    chromium_apply_config=['asan'],
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Release',
      'TARGET_BITS': 64,
    },
    bot_type='builder_tester',
    simulation_platform='mac',
  ),
  'ios': LibYUVBuilderSpec.create(
    chromium_config='libyuv_ios',
    gclient_config='libyuv_ios',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Debug',
      'TARGET_BITS': 32,
      'TARGET_ARCH': 'arm',
      'TARGET_PLATFORM': 'ios',
    },
    bot_type='builder',
    simulation_platform='mac',
    ensure_sdk='ios',
  ),
  'ios_rel': LibYUVBuilderSpec.create(
    chromium_config='libyuv_ios',
    gclient_config='libyuv_ios',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Release',
      'TARGET_BITS': 32,
      'TARGET_ARCH': 'arm',
      'TARGET_PLATFORM': 'ios',
    },
    bot_type='builder',
    simulation_platform='mac',
    ensure_sdk='ios',
  ),
  'ios_arm64': LibYUVBuilderSpec.create(
    chromium_config='libyuv_ios',
    gclient_config='libyuv_ios',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Debug',
      'TARGET_BITS': 64,
      'TARGET_ARCH': 'arm',
      'TARGET_PLATFORM': 'ios',
    },
    bot_type='builder',
    simulation_platform='mac',
    ensure_sdk='ios',
  ),
  'ios_arm64_rel': LibYUVBuilderSpec.create(
    chromium_config='libyuv_ios',
    gclient_config='libyuv_ios',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Release',
      'TARGET_BITS': 64,
      'TARGET_ARCH': 'arm',
      'TARGET_PLATFORM': 'ios',
    },
    bot_type='builder',
    simulation_platform='mac',
    ensure_sdk='ios',
  ),
  'linux': LibYUVBuilderSpec.create(
    chromium_config='libyuv',
    gclient_config='libyuv',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Debug',
      'TARGET_BITS': 64,
    },
    bot_type='builder_tester',
    simulation_platform='linux',
  ),
  'linux_rel': LibYUVBuilderSpec.create(
    chromium_config='libyuv',
    gclient_config='libyuv',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Release',
      'TARGET_BITS': 64,
    },
    bot_type='builder_tester',
    simulation_platform='linux',
  ),
  'linux_gcc': LibYUVBuilderSpec.create(
    chromium_config='libyuv_gcc',
    gclient_config='libyuv',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Release',
      'TARGET_BITS': 64,
    },
    bot_type='builder_tester',
    simulation_platform='linux',
  ),
  'linux_asan': LibYUVBuilderSpec.create(
    chromium_config='libyuv_clang',
    gclient_config='libyuv',
    chromium_apply_config=['asan', 'lsan'],
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Release',
      'TARGET_BITS': 64,
    },
    bot_type='builder_tester',
    simulation_platform='linux',
  ),
  'linux_msan': LibYUVBuilderSpec.create(
    chromium_config='libyuv_clang',
    gclient_config='libyuv',
    chromium_apply_config=['msan'],
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Release',
      'TARGET_BITS': 64,
    },
    bot_type='builder_tester',
    simulation_platform='linux',
  ),
  'linux_tsan2': LibYUVBuilderSpec.create(
    chromium_config='libyuv_clang',
    gclient_config='libyuv',
    chromium_apply_config=['tsan2'],
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Release',
      'TARGET_BITS': 64,
    },
    bot_type='builder_tester',
    simulation_platform='linux',
  ),
  'linux_ubsan': LibYUVBuilderSpec.create(
    chromium_config='libyuv_clang',
    gclient_config='libyuv',
    chromium_apply_config=['ubsan'],
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Release',
      'TARGET_BITS': 64,
    },
    bot_type='builder_tester',
    simulation_platform='linux',
  ),
  'linux_ubsan_vptr': LibYUVBuilderSpec.create(
    chromium_config='libyuv_clang',
    gclient_config='libyuv',
    chromium_apply_config=['ubsan_vptr'],
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Release',
      'TARGET_BITS': 64,
    },
    bot_type='builder_tester',
    simulation_platform='linux',
  ),
  'android': LibYUVBuilderSpec.create(
    chromium_config='libyuv',
    gclient_config='libyuv_android',
    android_config='libyuv',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Debug',
      'TARGET_PLATFORM': 'android',
      'TARGET_ARCH': 'arm',
      'TARGET_BITS': 32,
    },
    bot_type='builder_tester',
    simulation_platform='linux',
  ),
  'android_rel': LibYUVBuilderSpec.create(
    chromium_config='libyuv',
    gclient_config='libyuv_android',
    android_config='libyuv',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Release',
      'TARGET_PLATFORM': 'android',
      'TARGET_ARCH': 'arm',
      'TARGET_BITS': 32,
    },
    bot_type='builder_tester',
    simulation_platform='linux',
  ),
  'android_arm64': LibYUVBuilderSpec.create(
    chromium_config='libyuv',
    gclient_config='libyuv_android',
    android_config='libyuv',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Debug',
      'TARGET_PLATFORM': 'android',
      'TARGET_ARCH': 'arm',
      'TARGET_BITS': 64,
    },
    bot_type='builder_tester',
    simulation_platform='linux',
  ),
  'android_x86': LibYUVBuilderSpec.create(
    chromium_config='libyuv_android',
    gclient_config='libyuv_android',
    android_config='libyuv',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Debug',
      'TARGET_PLATFORM': 'android',
      'TARGET_ARCH': 'intel',
      'TARGET_BITS': 32,
    },
    bot_type='builder',
    simulation_platform='linux',
  ),
  'android_x64': LibYUVBuilderSpec.create(
    chromium_config='libyuv_android',
    gclient_config='libyuv_android',
    android_config='libyuv',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Debug',
      'TARGET_PLATFORM': 'android',
      'TARGET_ARCH': 'intel',
      'TARGET_BITS': 64,
    },
    bot_type='builder',
    simulation_platform='linux',
  ),
  'android_mips': LibYUVBuilderSpec.create(
    chromium_config='libyuv',
    gclient_config='libyuv_android',
    android_config='libyuv',
    chromium_config_kwargs={
      'BUILD_CONFIG': 'Debug',
      'TARGET_PLATFORM': 'android',
      'TARGET_ARCH': 'mipsel',
      'TARGET_BITS': 32,
    },
    bot_type='builder',
    simulation_platform='linux',
  ),
}

BUILDERS_DB = builder_db.BuilderDatabase.create(
  {
    'client.libyuv': _CLIENT_LIBYUV_SPEC,
    'tryserver.libyuv': _TRYSERVER_LIBYUV_SPEC,
  }
)
