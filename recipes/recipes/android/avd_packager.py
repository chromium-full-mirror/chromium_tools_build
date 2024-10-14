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
                  'avd_configs': ['some/proto/foo.textpb',],
                  'gclient_config': 'chromium',
                  'gclient_apply_config': ['android'],
              },
          }),
      api.post_process(post_process.MustRun,
                       'Process some/proto/foo.textpb.List AVD'),
      api.post_process(post_process.MustRun,
                       'Process some/proto/foo.textpb.Create AVD'),
      api.post_process(post_process.MustRun,
                       'Process some/proto/foo.textpb.Uninstall AVD'),
      api.post_process(post_process.DropExpectation),
  )
