# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

DEPS = [
    'chromium_3pp',
    'recipe_engine/properties',
]


def RunSteps(api):
  api.chromium_3pp.prepare()
  api.chromium_3pp.execute()


def GenTests(api):
  yield api.test(
      'basic',
      api.properties(
          **{
              '$build/chromium_3pp': {
                  'platform': 'linux-amd64',
                  'package_prefix': 'chromium',
                  'preprocess': [{
                      'name':
                          'third_party/foo',
                      'cmd': [
                          '{CHECKOUT}/src/third_party/foo/bar.py',
                          '--verbose',
                      ]
                  }],
                  'gclient_config': 'chromium',
                  'gclient_apply_config': ['android'],
              }
          }),
      api.post_process(
          post_process.MustRun,
          'Load all packages',
      ),
      api.post_process(
          post_process.MustRun,
          'Preprocessing third_party/foo',
      ),
      api.post_process(post_process.DropExpectation),
  )
