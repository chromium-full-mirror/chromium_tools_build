# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Packages Android AVDs as CIPD packages."""

from recipe_engine import post_process

DEPS = [
    'avd_packager',
    'recipe_engine/json',
    'recipe_engine/properties',
]


def RunSteps(api):
  api.avd_packager.prepare()
  api.avd_packager.execute()


def GenTests(api):
  yield api.test(
      'basic',
      api.properties(
          **{
              '$build/avd_packager': {
                  'avd_configs':
                      ['tools/android/avd/proto/generic_android28.textpb',],
                  'gclient_config': 'chromium',
                  'gclient_apply_config': ['android'],
              },
          }),
      api.post_process(
          post_process.MustRun,
          'avd create tools/android/avd/proto/generic_android28.textpb'),
      api.override_step_data(
          'avd create tools/android/avd/proto/generic_android28.textpb',
          api.json.output({
              'result': {
                  'instance_id': 'instance-id-generic-android-28',
                  'package': 'sample/avd/package/name',
              }
          })),
      api.post_process(post_process.MustRun,
                       'cipd set-tag sample/avd/package/name'),
      api.post_process(post_process.DropExpectation),
  )
