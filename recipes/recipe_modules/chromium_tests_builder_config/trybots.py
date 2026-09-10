# Copyright 2015 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from . import try_spec

# The config for the following builder groups are now specified src-side:
# * tryserver.blink
#   //infra/config/subprojects/chromium/try/tryserver.blink.star
# * tryserver.chromium
#   //infra/config/subprojects/chromium/try/tryserver.chromium.star
# * tryserver.chromium.android
#   //infra/config/subprojects/chromium/try/tryserver.chromium.android.star
# * tryserver.chromium.angle
#   //infra/config/subprojects/chromium/try/tryserver.chromium.angle.star
# * trserver.chromium.chromiumos
#   //infra/config/subprojects/chromium/try/tryserver.chromium.chromiumos.star
# * tryserver.chromium.dawn
#   //infra/config/subprojects/chromium/try/tryserver.chromium.dawn.star
# * tryserver.chromium.linux
#   //infra/config/subprojects/chromium/try/tryserver.chromium.linux.star
# * tryserver.chromium.mac
#   //infra/config/subprojects/chromium/try/tryserver.chromium.mac.star
# * tryserver.chromium.rust
#   //infra/config/subprojects/chromium/try/tryserver.chromium.rust.star
# * tryserver.chromium.swangle
#   //infra/config/subprojects/chromium/swangle.try.star
# * tryserver.chromium.win
#   //infra/config/subprojects/chromium/try/tryserver.chromium.win.star
TRYBOTS = try_spec.TryDatabase.create(
  {
    # The config for the following builders is now specified src-side in
    # //infra/config/subprojects/chromium/try/tryserver.chromium.updater.star
    # * mac-updater-try-builder-dbg
    # * mac-updater-try-builder-rel
    # * win-updater-try-builder-dbg
    # * win-updater-try-builder-rel
    'tryserver.chromium.updater': {},
    'tryserver.v8': {
      'v8_linux_chromium_gn_rel': try_spec.TrySpec.create_for_single_mirror(
        builder_group='client.v8.fyi',
        buildername='V8 Linux GN',
      ),
    },
    'tryserver.webrtc': {
      'win_chromium_compile': try_spec.TrySpec.create_for_single_mirror(
        builder_group='tryserver.webrtc',
        buildername='win_chromium_compile',
        is_compile_only=True,
        include_all_triggered_testers=True,
      ),
      'win_chromium_compile_dbg': try_spec.TrySpec.create_for_single_mirror(
        builder_group='tryserver.webrtc',
        buildername='win_chromium_compile_dbg',
        is_compile_only=True,
        include_all_triggered_testers=True,
      ),
      'mac_chromium_compile': try_spec.TrySpec.create_for_single_mirror(
        builder_group='tryserver.webrtc',
        buildername='mac_chromium_compile',
        is_compile_only=True,
        include_all_triggered_testers=True,
      ),
      'linux_chromium_compile': try_spec.TrySpec.create_for_single_mirror(
        builder_group='tryserver.webrtc',
        buildername='linux_chromium_compile',
        is_compile_only=True,
        include_all_triggered_testers=True,
      ),
      'linux_chromium_compile_dbg': try_spec.TrySpec.create_for_single_mirror(
        builder_group='tryserver.webrtc',
        buildername='linux_chromium_compile_dbg',
        is_compile_only=True,
        include_all_triggered_testers=True,
      ),
      'android_chromium_compile': try_spec.TrySpec.create_for_single_mirror(
        builder_group='tryserver.webrtc',
        buildername='android_chromium_compile',
        is_compile_only=True,
        include_all_triggered_testers=True,
      ),
      'webrtc_linux_chromium': try_spec.TrySpec.create_for_single_mirror(
        builder_group='tryserver.webrtc',
        buildername='webrtc_linux_chromium',
      ),
    },
    # These builders don't actually exist, the configs are created to provide a
    # known set of configs for integration testing the migration tracking
    # scripts
    'tryserver.migration.testing': {
      'foo': try_spec.TrySpec.create_for_single_mirror(
        builder_group='migration.testing',
        buildername='foo',
      ),
      'bar': try_spec.TrySpec.create_for_single_mirror(
        builder_group='migration.testing',
        buildername='bar',
      ),
    },
  }
)
