# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

DEPS = [
    'build',
]


def RunSteps(api):
  assert api.build.bot_utils_args


def GenTests(api):
  yield api.test(
      'basic',
      api.post_process(post_process.DropExpectation),
  )
