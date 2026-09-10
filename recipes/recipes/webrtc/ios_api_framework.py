# Copyright 2016 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
  builder_group,
  chromium,
  chromium_checkout,
  siso,
  webrtc,
)
from RECIPE_MODULES.depot_tools import gclient, tryserver
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  platform,
  properties,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  builder_group: builder_group.API
  chromium: chromium.API
  chromium_checkout: chromium_checkout.API
  gclient: gclient.API
  platform: platform.API
  properties: properties.API
  siso: siso.API
  step: step.API
  tryserver: tryserver.API
  webrtc: webrtc.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  builder_group: builder_group.TEST_API
  platform: platform.TEST_API
  properties: properties.TEST_API
  siso: siso.TEST_API


def RunSteps(api: DEPS):
  api.gclient.set_config('webrtc_ios')
  update_result = api.chromium_checkout.ensure_checkout()
  api.gclient.runhooks()

  api.chromium.set_config(
    'webrtc_default', TARGET_PLATFORM='ios', HOST_PLATFORM='mac'
  )
  api.chromium.apply_config('mac_toolchain')
  api.chromium.ensure_toolchains(update_result.checkout_dir)

  source_dir = update_result.source_root.path
  build_script = source_dir.joinpath('tools_webrtc', 'ios', 'build_ios_libs.py')
  cmd = ['vpython3', '-u', build_script, '--verbose', '--use-remoteexec']
  if not api.tryserver.is_tryserver:
    api.step('cleanup', [build_script, '-c'])
    cmd += ['-r', api.webrtc.revision_number]
  with api.siso.context():
    api.step('build', cmd)

  output_dir = source_dir / 'out_ios_libs'

  api.webrtc.get_binary_sizes(
    files=['WebRTC.xcframework/ios-arm64/WebRTC.framework/WebRTC'],
    build_dir=output_dir,
  )


def GenTests(api: TEST_DEPS):
  yield api.test(
    'build_ok',
    api.platform('mac', 64),
    api.builder_group.for_current('client.webrtc'),
    api.buildbucket.generic_build(builder='iOS API Framework Builder'),
    api.properties(xcode_build_version='dummy_xcode'),
    api.siso.properties(),
  )

  yield api.test(
    'build_failure',
    api.platform('mac', 64),
    api.builder_group.for_current('client.webrtc'),
    api.buildbucket.generic_build(builder='iOS API Framework Builder'),
    api.properties(xcode_build_version='dummy_xcode'),
    api.step_data('build', retcode=1),
    api.siso.properties(),
    status='FAILURE',
  )

  yield api.test(
    'trybot_build',
    api.platform('mac', 64),
    api.builder_group.for_current('tryserver.webrtc'),
    api.buildbucket.try_build(builder='ios_api_framework'),
    api.properties(
      xcode_build_version='dummy_xcode',
      gerrit_url='https://webrtc-review.googlesource.com',
      gerrit_project='src',
    ),
    api.siso.properties(),
  )
