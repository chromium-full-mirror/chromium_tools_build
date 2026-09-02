# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium_android
from RECIPE_MODULES.depot_tools import gclient
from RECIPE_MODULES.recipe_engine import properties


@dataclass
class DEPS(RecipeScriptApi):
  chromium_android: chromium_android.API
  gclient: gclient.API
  properties: properties.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  properties: properties.TEST_API

TEST_CONFIGS = [
    'arm',
    'arm64',
    'blink',
    'chrome_internal',
    'chromedriver',
    'chromium',
    'chromium_perf',
    'chromium_skia',
    'chromium_webrtc',
    'chromium_webrtc_tot',
    'fuchsia',
    'ios',
    'mac',
    'ndk_next',
    'openscreen_tot',
    'perf',
    'show_v8_revision',
    'v8_canary',
    'v8_tot',
    'webpagereplay',
    'webrtc_test_resources',
    'win',
]


def RunSteps(api: DEPS):
  for config_name in TEST_CONFIGS:
    api.gclient.make_config(config_name)

  api.gclient.set_config('chromium')
  api.gclient.apply_config(api.properties.get('apply_gclient_config'))


def GenTests(api: TEST_DEPS):
  yield api.test(
      'basic',
      api.properties(apply_gclient_config='checkout_instrumented_libraries'),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'clang_coverage',
      api.properties(apply_gclient_config='use_clang_coverage'),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'pgo_profiles',
      api.properties(apply_gclient_config='checkout_pgo_profiles'),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'enable_wpr_tests',
      api.properties(apply_gclient_config='enable_wpr_tests'),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'chromium_skip_wpr_archives_download',
      api.properties(
          apply_gclient_config='chromium_skip_wpr_archives_download'),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'checkout_bazel',
      api.properties(apply_gclient_config='checkout_bazel'),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'checkout_copybara',
      api.properties(apply_gclient_config='checkout_copybara'),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'checkout_src_internal_infra',
      api.properties(apply_gclient_config='checkout_src_internal_infra'),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'checkout_mutter',
      api.properties(apply_gclient_config='checkout_mutter'),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'checkout_mesa',
      api.properties(apply_gclient_config='checkout_mesa'),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'clang_tidy',
      api.properties(apply_gclient_config='use_clang_tidy'),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'tot_clang',
      api.properties(apply_gclient_config='clang_tot'),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'tot_rust',
      api.properties(apply_gclient_config='rust_tot'),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'no_generate_location_tags',
      api.properties(apply_gclient_config='no_generate_location_tags'),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'no_kaleidoscope',
      api.properties(apply_gclient_config='no_kaleidoscope'),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'enable_soda_integration_tests',
      api.properties(apply_gclient_config='enable_soda_integration_tests'),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'android_prebuilts_build_tools',
      api.properties(apply_gclient_config='android_prebuilts_build_tools'),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'ninja_staging',
      api.properties(apply_gclient_config='ninja_staging'),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'siso_latest',
      api.properties(apply_gclient_config='siso_latest'),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'fuchsia_arm64',
      api.properties(apply_gclient_config='fuchsia_arm64'),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'fuchsia_x64',
      api.properties(apply_gclient_config='fuchsia_x64'),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'fuchsia_netstack2_x64',
      api.properties(apply_gclient_config='fuchsia_netstack2_x64'),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'fuchsia_no_hooks',
      api.properties(apply_gclient_config='fuchsia_no_hooks'),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'fuchsia_arm64_host',
      api.properties(apply_gclient_config='fuchsia_arm64_host'),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'fuchsia_internal',
      api.properties(apply_gclient_config='fuchsia_internal'),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'fuchsia_astro_image',
      api.properties(apply_gclient_config='fuchsia_astro_image'),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'fuchsia_sherlock_image',
      api.properties(apply_gclient_config='fuchsia_sherlock_image'),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'fuchsia_sd_images',
      api.properties(apply_gclient_config='fuchsia_sd_images'),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'fuchsia_workstation',
      api.properties(apply_gclient_config='fuchsia_workstation'),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'fuchsia_atlas',
      api.properties(apply_gclient_config='fuchsia_atlas'),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'fuchsia_workstation_perf_images',
      api.properties(apply_gclient_config='fuchsia_workstation_perf_images'),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'chromium_with_telemetry_dependencies',
      api.properties(
          apply_gclient_config='chromium_with_telemetry_dependencies'),
      api.post_process(post_process.DropExpectation),
  )
