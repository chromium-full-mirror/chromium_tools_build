# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process


DEPS = [
    'chromium_checkout',
    'profiles',
    'recipe_engine/assertions',
    'recipe_engine/path',
]


def RunSteps(api):
  # fake path for start_dir
  api.profiles.upload(
      'bucket',
      'path/artifact.txt',
      '/local/tmp/artifact.txt',
      args=['-Z'],
      link_name='artifact.txt')


def GenTests(api):

  yield api.test(
      'basic',
      api.post_process(post_process.MustRun, 'gsutil upload artifact to GS'),
      api.post_process(post_process.DropExpectation),
  )
