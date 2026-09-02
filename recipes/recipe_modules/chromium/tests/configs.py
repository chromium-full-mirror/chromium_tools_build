# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium
from RECIPE_MODULES.recipe_engine import platform, properties


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  platform: platform.API
  properties: properties.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  platform: platform.TEST_API
  properties: properties.TEST_API


def RunSteps(api: DEPS):
  api.chromium.set_config(
      api.properties.get('chromium_config', 'chromium'),
      TARGET_PLATFORM=api.properties.get('target_platform', 'linux'),
      TARGET_BITS=api.properties.get('target_bits', 64))

  api.chromium.verify_config |= True

  for config in api.properties.get('chromium_apply_config', []):
    api.chromium.apply_config(config)


def GenTests(api: TEST_DEPS):
  def from_config(config):
    return api.test(
        config,
        api.properties(chromium_config=config),
        api.post_process(post_process.DropExpectation),
    )

  yield from_config('gn')

  yield api.test(
      'ios',
      api.platform('mac', 64),
      api.properties(target_platform='ios'),
      api.post_process(post_process.DropExpectation),
  )

  yield from_config('gcc')

  yield from_config('trybot_flavor')

  yield api.test(
      'chromium_win_clang_official',
      api.platform('win', 64),
      api.properties(
          chromium_config='chromium_win_clang_official', target_platform='win'),
      api.post_process(post_process.DropExpectation),
  )

  yield from_config('chromium_win_clang_official_tot')

  yield from_config('chromium_win_clang_asan')

  yield from_config('chromium_win_clang_asan_tot')

  yield from_config('clang_tot_linux')

  yield from_config('rust_tot_linux')

  yield from_config('rust_tot_mac')

  yield from_config('chromium_win_rust_tot')

  yield api.test(
      'clang_tot_mac',
      api.platform('mac', 64),
      api.properties(
          chromium_config='clang_tot_mac_asan', target_platform='mac'),
      api.post_process(post_process.DropExpectation),
  )

  yield from_config('clang_tot_ios')

  yield from_config('clang_tot_linux_asan')

  yield from_config('chromium_linux_ubsan')

  yield from_config('chromium_linux_ubsan_vptr')

  yield from_config('clang_tot_linux_ubsan_vptr')

  yield api.test(
      'clang_tot_mac_asan',
      api.platform('mac', 64),
      api.properties(
          chromium_config='clang_tot_mac_asan', target_platform='mac'),
      api.post_process(post_process.DropExpectation),
  )

  yield from_config('clang_tot_android_asan')

  yield from_config('clang_tot_android_dbg')

  yield from_config('clang_tot_chromeos')

  yield from_config('clang_tot_fuchsia')

  yield from_config('chromium_tsan2')

  yield from_config('android_clang')

  yield from_config('android_asan')

  yield from_config('download_xr_test_apks')

  yield from_config('internal_isolate_paths')

  yield from_config('internal_targets_specs')

  yield from_config('internal_mb_config')

  yield api.test(
      'mac_toolchain',
      api.platform('mac', 64),
      api.properties(
          target_platform='mac',
          chromium_apply_config=['mac_toolchain'],
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'ios_toolchain',
      api.platform('mac', 64),
      api.properties(
          target_platform='ios',
          chromium_apply_config=['mac_toolchain'],
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield from_config('android_internal_isolate_maps')

  yield from_config('official_no_clobber')

  yield api.test(
      'ios_codesearch',
      api.platform('mac', 64),
      api.properties(
          target_platform='ios',
          chromium_config='codesearch',
      ),
      api.post_process(post_process.DropExpectation),
  )
