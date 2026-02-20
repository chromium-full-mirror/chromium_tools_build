# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

# Contains the bulk of the WebRTC builder configurations so they can be reused
# from multiple recipes.

from __future__ import annotations

from RECIPE_MODULES.build.attr_utils import (attrib, attrs)
from RECIPE_MODULES.build.chromium_tests_builder_config import (builder_db,
                                                                builder_spec)

@attrs()
class WebRTCBuilderSpec(builder_spec.BuilderSpec):
  perf_id = attrib(str, default=None)
  binary_size_files = attrib(tuple, default=None)
  archive_apprtc = attrib(bool, default=False)
  build_android_archive = attrib(bool, default=False)
  include_cleaner = attrib(bool, default=False)
  phases = attrib(tuple, default=(None,))


_CLIENT_WEBRTC_SPEC = {
    'Android32':
        WebRTCBuilderSpec.create(
            archive_apprtc=True,
            build_android_archive=True,
            chromium_config='webrtc_android',
            android_config='webrtc',
            gclient_config='webrtc',
            gclient_apply_config=['android'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_PLATFORM': 'android',
                'TARGET_ARCH': 'arm',
                'TARGET_BITS': 32,
            }),
    'Android32 (dbg)':
        WebRTCBuilderSpec.create(
            archive_apprtc=True,
            chromium_config='webrtc_android',
            android_config='webrtc',
            gclient_config='webrtc',
            gclient_apply_config=['android'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Debug',
                'TARGET_PLATFORM': 'android',
                'TARGET_ARCH': 'arm',
                'TARGET_BITS': 32,
            }),
    'Android32 (more configs)':
        WebRTCBuilderSpec.create(
            phases=(
                'cxx23',
                'dummy_audio_file_devices_no_protobuf',
                'rtti_no_sctp',
            ),
            chromium_config='webrtc_android',
            android_config='webrtc',
            gclient_config='webrtc',
            gclient_apply_config=['android'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Debug',
                'TARGET_PLATFORM': 'android',
                'TARGET_ARCH': 'arm',
                'TARGET_BITS': 32,
            }),
    'Android32 Builder arm':
        WebRTCBuilderSpec.create(
            binary_size_files=('libjingle_peerconnection_so.so',
                               'apks/AppRTCMobile.apk'),
            chromium_config='webrtc_android',
            android_config='webrtc',
            gclient_config='webrtc',
            gclient_apply_config=['android'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_PLATFORM': 'android',
                'TARGET_ARCH': 'arm',
                'TARGET_BITS': 32,
            }),
    'Android32 Builder x86':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_android',
            android_config='webrtc',
            gclient_config='webrtc',
            gclient_apply_config=['android'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_PLATFORM': 'android',
                'TARGET_ARCH': 'intel',
                'TARGET_BITS': 32,
            }),
    'Android32 Builder x86 (dbg)':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_android',
            android_config='webrtc',
            gclient_config='webrtc',
            gclient_apply_config=['android'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Debug',
                'TARGET_PLATFORM': 'android',
                'TARGET_ARCH': 'intel',
                'TARGET_BITS': 32,
            }),
    'Android64':
        WebRTCBuilderSpec.create(
            archive_apprtc=True,
            chromium_config='webrtc_android',
            android_config='webrtc',
            gclient_config='webrtc',
            gclient_apply_config=['android'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_PLATFORM': 'android',
                'TARGET_ARCH': 'arm',
                'TARGET_BITS': 64,
            }),
    'Android64 (dbg)':
        WebRTCBuilderSpec.create(
            archive_apprtc=True,
            chromium_config='webrtc_android',
            android_config='webrtc',
            gclient_config='webrtc',
            gclient_apply_config=['android'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Debug',
                'TARGET_PLATFORM': 'android',
                'TARGET_ARCH': 'arm',
                'TARGET_BITS': 64,
            }),
    'Android64 Builder arm64':
        WebRTCBuilderSpec.create(
            binary_size_files=('libjingle_peerconnection_so.so',
                               'apks/AppRTCMobile.apk'),
            chromium_config='webrtc_android',
            android_config='webrtc',
            gclient_config='webrtc',
            gclient_apply_config=['android'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_PLATFORM': 'android',
                'TARGET_ARCH': 'arm',
                'TARGET_BITS': 64,
            }),
    'Android64 Builder x64 (dbg)':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_android',
            android_config='webrtc',
            gclient_config='webrtc',
            gclient_apply_config=['android'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Debug',
                'TARGET_PLATFORM': 'android',
                'TARGET_ARCH': 'intel',
                'TARGET_BITS': 64,
            }),
    'Fuchsia Builder':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_default',
            gclient_config='webrtc_fuchsia',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            }),
    'Fuchsia Release':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_default',
            gclient_config='webrtc_fuchsia',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            }),
    'Linux (more configs)':
        WebRTCBuilderSpec.create(
            phases=(
                'cxx23',
                'dummy_audio_file_devices_no_protobuf',
                'rtti_no_sctp',
                'disable_trace_events',
                'openssl',
                'perfetto',
            ),
            chromium_config='webrtc_default',
            gclient_config='webrtc',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Debug',
                'TARGET_BITS': 64,
            }),
    'Linux Asan':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_clang',
            gclient_config='webrtc',
            chromium_apply_config=['asan', 'lsan'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            }),
    'Linux MSan':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_clang',
            gclient_config='webrtc',
            chromium_apply_config=['msan'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            }),
    'Linux Tsan v2':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_clang',
            gclient_config='webrtc',
            chromium_apply_config=['tsan2'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            }),
    'Linux UBSan':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_clang',
            gclient_config='webrtc',
            chromium_apply_config=['ubsan'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            }),
    'Linux UBSan vptr':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_clang',
            gclient_config='webrtc',
            chromium_apply_config=['ubsan_vptr'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            }),
    'Linux32 Debug':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_default',
            gclient_config='webrtc',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Debug',
                'TARGET_BITS': 32,
            }),
    'Linux32 Debug (ARM)':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_default',
            gclient_config='webrtc',
            gclient_apply_config=['arm'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Debug',
                'TARGET_ARCH': 'arm',
                'TARGET_BITS': 32,
            }),
    'Linux32 Release':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_default',
            gclient_config='webrtc',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 32,
            }),
    'Linux32 Release (ARM)':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_default',
            gclient_config='webrtc',
            gclient_apply_config=['arm'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_ARCH': 'arm',
                'TARGET_BITS': 32,
            }),
    'Linux64 Debug':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_default',
            gclient_config='webrtc',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Debug',
                'TARGET_BITS': 64,
            }),
    'Linux64 Debug (ARM)':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_default',
            gclient_config='webrtc',
            gclient_apply_config=['arm64'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Debug',
                'TARGET_ARCH': 'arm',
                'TARGET_BITS': 64,
            }),
    'Linux64 Builder':
        WebRTCBuilderSpec.create(
            binary_size_files=('obj/libwebrtc.a',),
            chromium_config='webrtc_default',
            gclient_config='webrtc',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            }),
    'Linux64 Release':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_default',
            gclient_config='webrtc',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            }),
    'Linux64 Release (ARM)':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_default',
            gclient_config='webrtc',
            gclient_apply_config=['arm64'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_ARCH': 'arm',
                'TARGET_BITS': 64,
            }),
    'Mac Asan':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_clang',
            gclient_config='webrtc',
            chromium_apply_config=['asan'],
            simulation_platform='mac',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            }),
    'MacArm64 Builder':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_default',
            gclient_config='webrtc_linux_mac_crosscompile_arm64',
            simulation_platform='mac',
            chromium_config_kwargs={
                'TARGET_ARCH': 'arm',
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            }),
    'Mac64 Debug':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_default',
            gclient_config='webrtc',
            simulation_platform='mac',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Debug',
                'TARGET_BITS': 64,
            }),
    'Mac64 Release':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_default',
            gclient_config='webrtc',
            simulation_platform='mac',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            }),
    'MacARM64 M1 Release':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_default',
            gclient_config='webrtc',
            simulation_platform='mac',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            }),
    'Win (more configs)':
        WebRTCBuilderSpec.create(
            phases=(
                'cxx23',
                'dummy_audio_file_devices_no_protobuf',
                'rtti_no_sctp',
            ),
            chromium_config='webrtc_default',
            gclient_config='webrtc',
            simulation_platform='win',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Debug',
                'TARGET_BITS': 64,
            }),
    'Win64 Builder (Clang)':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_clang',
            gclient_config='webrtc',
            simulation_platform='win',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            }),
    'Win32 Debug (Clang)':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_clang',
            gclient_config='webrtc',
            simulation_platform='win',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Debug',
                'TARGET_BITS': 32,
            }),
    'Win32 Release (Clang)':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_clang',
            gclient_config='webrtc',
            simulation_platform='win',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 32,
            }),
    'Win64 ASan':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_clang',
            gclient_config='webrtc',
            chromium_apply_config=['asan'],
            simulation_platform='win',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            }),
    'Win64 Debug (Clang)':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_clang',
            gclient_config='webrtc',
            simulation_platform='win',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Debug',
                'TARGET_BITS': 64,
            }),
    'Win64 Release (Clang)':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_clang',
            gclient_config='webrtc',
            simulation_platform='win',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            }),
    'iOS64 Debug':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_default',
            gclient_config='webrtc_ios',
            chromium_apply_config=['mac_toolchain'],
            simulation_platform='mac',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Debug',
                'TARGET_PLATFORM': 'ios',
                'TARGET_ARCH': 'arm',
                'TARGET_BITS': 64,
            }),
    'iOS64 Release':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_default',
            gclient_config='webrtc_ios',
            chromium_apply_config=['mac_toolchain'],
            simulation_platform='mac',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_PLATFORM': 'ios',
                'TARGET_ARCH': 'arm',
                'TARGET_BITS': 64,
            }),
    'iOS Debug (simulator)':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_default',
            gclient_config='webrtc_ios',
            chromium_apply_config=['mac_toolchain'],
            simulation_platform='mac',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Debug',
                'TARGET_PLATFORM': 'ios',
                'TARGET_ARCH': 'intel',
                'TARGET_BITS': 64,
            }),
}

_CLIENT_WEBRTC_PERF_SPECS = {
    'Perf Android32 (R Pixel5)':
        WebRTCBuilderSpec.create(
            perf_id='android32-pixel5-android11',
            chromium_config='webrtc_default',
            android_config='webrtc',
            gclient_config='webrtc',
            gclient_apply_config=['android'],
            execution_mode=builder_spec.TEST,
            parent_builder_group='client.webrtc',
            parent_buildername='Android32 Builder arm',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_PLATFORM': 'android',
                'TARGET_ARCH': 'arm',
                'TARGET_BITS': 32,
            }),
    'Perf Android64 (R Pixel5)':
        WebRTCBuilderSpec.create(
            perf_id='android-pixel5-android11',
            chromium_config='webrtc_default',
            android_config='webrtc',
            gclient_config='webrtc',
            gclient_apply_config=['android'],
            execution_mode=builder_spec.TEST,
            parent_builder_group='client.webrtc',
            parent_buildername='Android64 Builder arm64',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_PLATFORM': 'android',
                'TARGET_ARCH': 'arm',
                'TARGET_BITS': 64,
            }),
    'Perf Fuchsia':
        WebRTCBuilderSpec.create(
            perf_id='fuchsia',
            chromium_config='webrtc_default',
            gclient_config='webrtc_fuchsia',
            execution_mode=builder_spec.TEST,
            parent_builder_group='client.webrtc',
            parent_buildername='Fuchsia Builder',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            }),
    'Perf Linux':
        WebRTCBuilderSpec.create(
            perf_id='webrtc-linux-tests',
            chromium_config='webrtc_default',
            gclient_config='webrtc',
            execution_mode=builder_spec.TEST,
            parent_builder_group='client.webrtc',
            parent_buildername='Linux64 Builder',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            }),
    'Perf Mac M1 Arm64 12':
        WebRTCBuilderSpec.create(
            perf_id='webrtc-mac-arm64-large-tests',
            chromium_config='webrtc_default',
            gclient_config='webrtc',
            execution_mode=builder_spec.TEST,
            parent_builder_group='client.webrtc',
            parent_buildername='MacArm64 Builder',
            simulation_platform='mac',
            chromium_config_kwargs={
                'TARGET_ARCH': 'arm',
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            }),
    'Perf Win 10':
        WebRTCBuilderSpec.create(
            perf_id='webrtc-win-large-tests',
            chromium_config='webrtc_default',
            gclient_config='webrtc',
            execution_mode=builder_spec.TEST,
            parent_builder_group='client.webrtc',
            parent_buildername='Win64 Builder (Clang)',
            simulation_platform='win',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            }),
}

_INTERNAL_CLIENT_WEBRTC_SPECS = {
    'iOS64 Debug':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_default',
            gclient_config='webrtc_ios',
            chromium_apply_config=['mac_toolchain'],
            simulation_platform='mac',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Debug',
                'TARGET_PLATFORM': 'ios',
                'TARGET_ARCH': 'arm',
                'TARGET_BITS': 64,
            }),
    'iOS64 Perf':
        WebRTCBuilderSpec.create(
            perf_id='webrtc-ios-tests',
            chromium_config='webrtc_default',
            gclient_config='webrtc_ios',
            chromium_apply_config=['mac_toolchain'],
            simulation_platform='mac',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_PLATFORM': 'ios',
                'TARGET_ARCH': 'arm',
                'TARGET_BITS': 64,
            }),
    'iOS64 Release':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_default',
            gclient_config='webrtc_ios',
            chromium_apply_config=['mac_toolchain'],
            simulation_platform='mac',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_PLATFORM': 'ios',
                'TARGET_ARCH': 'arm',
                'TARGET_BITS': 64,
            }),
}

_TRYSERVER_WEBRTC_SPEC = {
    'android_compile_arm_dbg':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_android',
            android_config='webrtc',
            gclient_config='webrtc',
            gclient_apply_config=['android'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Debug',
                'TARGET_PLATFORM': 'android',
                'TARGET_ARCH': 'arm',
                'TARGET_BITS': 32,
            }),
    'android_compile_arm_rel':
        WebRTCBuilderSpec.create(
            binary_size_files=('libjingle_peerconnection_so.so',
                               'apks/AppRTCMobile.apk'),
            chromium_config='webrtc_android',
            android_config='webrtc',
            gclient_config='webrtc',
            gclient_apply_config=['android'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_PLATFORM': 'android',
                'TARGET_ARCH': 'arm',
                'TARGET_BITS': 32,
            }),
    'android_compile_arm64_dbg':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_android',
            android_config='webrtc',
            gclient_config='webrtc',
            gclient_apply_config=['android'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Debug',
                'TARGET_PLATFORM': 'android',
                'TARGET_ARCH': 'arm',
                'TARGET_BITS': 64,
            }),
    'android_compile_arm64_rel':
        WebRTCBuilderSpec.create(
            binary_size_files=('libjingle_peerconnection_so.so',
                               'apks/AppRTCMobile.apk'),
            chromium_config='webrtc_android',
            android_config='webrtc',
            gclient_config='webrtc',
            gclient_apply_config=['android'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_PLATFORM': 'android',
                'TARGET_ARCH': 'arm',
                'TARGET_BITS': 64,
            }),
    'android_compile_x86_dbg':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_android',
            android_config='webrtc',
            gclient_config='webrtc',
            gclient_apply_config=['android'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Debug',
                'TARGET_PLATFORM': 'android',
                'TARGET_ARCH': 'intel',
                'TARGET_BITS': 32,
            }),
    'android_compile_x86_rel':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_android',
            android_config='webrtc',
            gclient_config='webrtc',
            gclient_apply_config=['android'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_PLATFORM': 'android',
                'TARGET_ARCH': 'intel',
                'TARGET_BITS': 32,
            }),
    'android_compile_x64_dbg':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_android',
            android_config='webrtc',
            gclient_config='webrtc',
            gclient_apply_config=['android'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Debug',
                'TARGET_PLATFORM': 'android',
                'TARGET_ARCH': 'intel',
                'TARGET_BITS': 64,
            }),
    'android_compile_x64_rel':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_android',
            android_config='webrtc',
            gclient_config='webrtc',
            gclient_apply_config=['android'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_PLATFORM': 'android',
                'TARGET_ARCH': 'intel',
                'TARGET_BITS': 64,
            }),
    'android_arm_dbg':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_android',
            android_config='webrtc',
            gclient_config='webrtc',
            gclient_apply_config=['android'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Debug',
                'TARGET_PLATFORM': 'android',
                'TARGET_ARCH': 'arm',
                'TARGET_BITS': 32,
            }),
    'android_arm_more_configs':
        WebRTCBuilderSpec.create(
            phases=(
                'cxx23',
                'dummy_audio_file_devices_no_protobuf',
                'rtti_no_sctp',
                'disable_trace_events',
            ),
            chromium_config='webrtc_android',
            gclient_config='webrtc',
            gclient_apply_config=['android'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Debug',
                'TARGET_PLATFORM': 'android',
                'TARGET_ARCH': 'arm',
                'TARGET_BITS': 32,
            }),
    'android_arm_rel':
        WebRTCBuilderSpec.create(
            build_android_archive=True,
            chromium_config='webrtc_android',
            android_config='webrtc',
            gclient_config='webrtc',
            gclient_apply_config=['android'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_PLATFORM': 'android',
                'TARGET_ARCH': 'arm',
                'TARGET_BITS': 32,
            }),
    'android_arm64_dbg':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_android',
            android_config='webrtc',
            gclient_config='webrtc',
            gclient_apply_config=['android'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Debug',
                'TARGET_PLATFORM': 'android',
                'TARGET_ARCH': 'arm',
                'TARGET_BITS': 64,
            }),
    'android_arm64_rel':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_android',
            android_config='webrtc',
            gclient_config='webrtc',
            gclient_apply_config=['android'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_PLATFORM': 'android',
                'TARGET_ARCH': 'arm',
                'TARGET_BITS': 64,
            }),
    'fuchsia_rel':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_default',
            gclient_config='webrtc_fuchsia',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            }),
    'ios_compile_arm64_dbg':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_default',
            gclient_config='webrtc_ios',
            chromium_apply_config=['mac_toolchain'],
            simulation_platform='mac',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Debug',
                'TARGET_PLATFORM': 'ios',
                'TARGET_ARCH': 'arm',
                'TARGET_BITS': 64,
            }),
    'ios_compile_arm64_rel':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_default',
            gclient_config='webrtc_ios',
            chromium_apply_config=['mac_toolchain'],
            simulation_platform='mac',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_PLATFORM': 'ios',
                'TARGET_ARCH': 'arm',
                'TARGET_BITS': 64,
            }),
    'ios_dbg_simulator':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_default',
            gclient_config='webrtc_ios',
            chromium_apply_config=['mac_toolchain'],
            simulation_platform='mac',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Debug',
                'TARGET_PLATFORM': 'ios',
                'TARGET_ARCH': 'intel',
                'TARGET_BITS': 64,
            }),
    'iwyu_verifier':
        WebRTCBuilderSpec.create(
            include_cleaner=True,
            chromium_config='webrtc_default',
            gclient_config='webrtc_use_include_cleaner',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            }),
    'linux_asan':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_clang',
            gclient_config='webrtc',
            chromium_apply_config=['asan', 'lsan'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            }),
    'linux_compile_arm_dbg':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_default',
            gclient_config='webrtc',
            gclient_apply_config=['arm'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Debug',
                'TARGET_ARCH': 'arm',
                'TARGET_BITS': 32,
            }),
    'linux_compile_arm_rel':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_default',
            gclient_config='webrtc',
            gclient_apply_config=['arm'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_ARCH': 'arm',
                'TARGET_BITS': 32,
            }),
    'linux_compile_arm64_dbg':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_default',
            gclient_config='webrtc',
            gclient_apply_config=['arm64'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Debug',
                'TARGET_ARCH': 'arm',
                'TARGET_BITS': 64,
            }),
    'linux_compile_arm64_rel':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_default',
            gclient_config='webrtc',
            gclient_apply_config=['arm64'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_ARCH': 'arm',
                'TARGET_BITS': 64,
            }),
    'linux_compile_dbg':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_default',
            gclient_config='webrtc',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Debug',
                'TARGET_BITS': 64,
            }),
    'linux_compile_rel':
        WebRTCBuilderSpec.create(
            binary_size_files=('obj/libwebrtc.a',),
            chromium_config='webrtc_default',
            gclient_config='webrtc',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            }),
    'linux_coverage':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_default',
            gclient_config='webrtc_use_clang_coverage',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            }),
    'linux_dbg':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_default',
            gclient_config='webrtc',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Debug',
                'TARGET_BITS': 64,
            }),
    'linux_more_configs':
        WebRTCBuilderSpec.create(
            phases=(
                'cxx23',
                'dummy_audio_file_devices_no_protobuf',
                'rtti_no_sctp',
                'disable_trace_events',
                'openssl',
                'perfetto',
            ),
            chromium_config='webrtc_default',
            gclient_config='webrtc',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Debug',
                'TARGET_BITS': 64,
            }),
    'linux_msan':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_clang',
            gclient_config='webrtc',
            chromium_apply_config=['msan'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            }),
    'linux_rel':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_default',
            gclient_config='webrtc',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            }),
    'linux_tsan2':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_clang',
            gclient_config='webrtc',
            chromium_apply_config=['tsan2'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            }),
    'linux_ubsan':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_clang',
            gclient_config='webrtc',
            chromium_apply_config=['ubsan'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            }),
    'linux_ubsan_vptr':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_clang',
            gclient_config='webrtc',
            chromium_apply_config=['ubsan_vptr'],
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            }),
    'linux_x86_dbg':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_default',
            gclient_config='webrtc',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Debug',
                'TARGET_BITS': 32,
            }),
    'linux_x86_rel':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_default',
            gclient_config='webrtc',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 32,
            }),
    'mac_asan':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_clang',
            gclient_config='webrtc',
            chromium_apply_config=['asan'],
            simulation_platform='mac',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            }),
    'mac_compile_dbg':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_default',
            gclient_config='webrtc',
            simulation_platform='mac',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Debug',
                'TARGET_BITS': 64,
            }),
    'mac_compile_rel':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_default',
            gclient_config='webrtc',
            simulation_platform='mac',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            }),
    'mac_dbg':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_default',
            gclient_config='webrtc',
            simulation_platform='mac',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Debug',
                'TARGET_BITS': 64,
            }),
    'mac_dbg_m1':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_default',
            gclient_config='webrtc',
            simulation_platform='mac',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Debug',
                'TARGET_BITS': 64,
                'TARGET_ARCH': 'arm'
            }),
    'mac_rel':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_default',
            gclient_config='webrtc',
            simulation_platform='mac',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            }),
    'mac_rel_m1':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_default',
            gclient_config='webrtc',
            simulation_platform='mac',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
                'TARGET_ARCH': 'arm'
            }),
    'win_asan':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_clang',
            gclient_config='webrtc',
            chromium_apply_config=['asan'],
            simulation_platform='win',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            }),
    'win_compile_x64_clang_dbg':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_clang',
            gclient_config='webrtc',
            simulation_platform='win',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Debug',
                'TARGET_BITS': 64,
            }),
    'win_compile_x64_clang_rel':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_clang',
            gclient_config='webrtc',
            simulation_platform='win',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            }),
    'win_compile_x86_clang_dbg':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_clang',
            gclient_config='webrtc',
            simulation_platform='win',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Debug',
                'TARGET_BITS': 32,
            }),
    'win_compile_x86_clang_rel':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_clang',
            gclient_config='webrtc',
            simulation_platform='win',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 32,
            }),
    'win_x64_clang_dbg':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_clang',
            gclient_config='webrtc',
            simulation_platform='win',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Debug',
                'TARGET_BITS': 64,
            }),
    'win_x64_clang_rel':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_clang',
            gclient_config='webrtc',
            simulation_platform='win',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            }),
    'win_x86_clang_dbg':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_clang',
            gclient_config='webrtc',
            simulation_platform='win',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Debug',
                'TARGET_BITS': 32,
            }),
    'win_x86_clang_rel':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_clang',
            gclient_config='webrtc',
            simulation_platform='win',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 32,
            }),
    'win_x86_more_configs':
        WebRTCBuilderSpec.create(
            phases=(
                'cxx23',
                'dummy_audio_file_devices_no_protobuf',
                'rtti_no_sctp',
                'disable_trace_events',
            ),
            chromium_config='webrtc_default',
            gclient_config='webrtc',
            simulation_platform='win',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Debug',
                'TARGET_BITS': 64,
            }),
    'win11_release':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_clang',
            gclient_config='webrtc',
            simulation_platform='win',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 64,
            }),
    'win11_debug':
        WebRTCBuilderSpec.create(
            chromium_config='webrtc_clang',
            gclient_config='webrtc',
            simulation_platform='win',
            chromium_config_kwargs={
                'BUILD_CONFIG': 'Debug',
                'TARGET_BITS': 64,
            }),
}

BUILDERS_DB = builder_db.BuilderDatabase.create({
    'client.webrtc': _CLIENT_WEBRTC_SPEC,
    'client.webrtc.perf': _CLIENT_WEBRTC_PERF_SPECS,
    'internal.client.webrtc': _INTERNAL_CLIENT_WEBRTC_SPECS,
    'tryserver.webrtc': _TRYSERVER_WEBRTC_SPEC,
})
