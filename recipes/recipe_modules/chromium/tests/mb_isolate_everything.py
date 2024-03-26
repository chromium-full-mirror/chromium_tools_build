# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

from RECIPE_MODULES.build import chromium

DEPS = [
    'chromium',
    'recipe_engine/path',
]


def RunSteps(api):
  api.chromium.set_config('chromium')
  api.path['checkout'] = api.path['cache'] / 'builder' / 'src'
  api.chromium.mb_isolate_everything(
      chromium.BuilderId.create_for_group('test-group', 'test-builder'))


def GenTests(api):
  yield api.test(
      'basic',
      api.post_process(post_process.StepCommandRE, 'generate .isolate files', [
          'python3', '-u', r'.*/tools/mb/mb\.py', 'isolate-everything', '-m',
          'test-group', '-b', 'test-builder', '--config-file',
          r'.*/tools/mb/mb_config\.pyl', '//out/Release'
      ]),
      api.post_process(post_process.DropExpectation),
  )
