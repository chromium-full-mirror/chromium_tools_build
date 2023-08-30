# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

DEPS = [
    'chromium_rts',
    'depot_tools/gclient',
]


def RunSteps(api):
  api.chromium_rts.rts_setting = 'rts-chromium'
  assert api.chromium_rts.rts_setting == 'rts-chromium'
  api.chromium_rts.rts_recall = .99
  assert api.chromium_rts.rts_recall == .99

  api.gclient.set_config('chromium')
  api.chromium_rts.configure_build()

  api.chromium_rts.rts_setting = 'rts-ml-chromium'
  api.chromium_rts.configure_build()


def GenTests(api):
  yield api.test(
      'basic',
      api.post_process(post_process.DropExpectation),
  )
