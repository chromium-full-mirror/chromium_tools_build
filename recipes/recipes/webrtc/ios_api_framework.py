# Copyright 2016 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

DEPS = [
    'builder_group',
    'chromium',
    'chromium_checkout',
    'depot_tools/gclient',
    'depot_tools/tryserver',
    'recipe_engine/buildbucket',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/step',
    'reclient',
    'webrtc',
]


def RunSteps(api):
  api.gclient.set_config('webrtc_ios')
  api.chromium_checkout.ensure_checkout()
  api.gclient.runhooks()

  api.chromium.set_config(
      'webrtc_default', TARGET_PLATFORM='ios', HOST_PLATFORM='mac')
  api.chromium.apply_config('mac_toolchain')
  api.chromium.ensure_toolchains()

  build_script = api.path.checkout_dir.join('tools_webrtc', 'ios',
                                            'build_ios_libs.py')
  cmd = ['vpython3', '-u', build_script, '--verbose']
  if not api.tryserver.is_tryserver:
    api.step('cleanup', [build_script, '-c'])
    cmd += ['-r', api.webrtc.revision_number]
  api.webrtc.build_with_reclient('build', cmd)

  output_dir = api.path.checkout_dir.join('out_ios_libs')

  api.webrtc.get_binary_sizes(
      files=['WebRTC.xcframework/ios-arm64/WebRTC.framework/WebRTC'],
      base_dir=output_dir)


def GenTests(api):
  yield api.test(
      'build_ok',
      api.builder_group.for_current('client.webrtc'),
      api.buildbucket.generic_build(builder='iOS API Framework Builder'),
      api.properties(xcode_build_version='dummy_xcode'),
      api.reclient.properties(),
  )

  yield api.test(
      'build_failure',
      api.builder_group.for_current('client.webrtc'),
      api.buildbucket.generic_build(builder='iOS API Framework Builder'),
      api.properties(xcode_build_version='dummy_xcode'),
      api.step_data('build', retcode=1),
      api.reclient.properties(),
      status='FAILURE',
  )

  yield api.test(
      'trybot_build',
      api.builder_group.for_current('tryserver.webrtc'),
      api.buildbucket.try_build(builder='ios_api_framework'),
      api.properties(
          xcode_build_version='dummy_xcode',
          gerrit_url='https://webrtc-review.googlesource.com',
          gerrit_project='src'),
      api.reclient.properties(),
  )
