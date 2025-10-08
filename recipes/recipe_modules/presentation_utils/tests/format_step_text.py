# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

DEPS = [
    'presentation_utils',
    'recipe_engine/properties',
]


def RunSteps(api):
  data = api.properties['data']
  api.presentation_utils.format_step_text(data)


def GenTests(api):
  yield api.test(
      'basic',
      api.properties(data=[('header', 'body')]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'too_many_elements',
      api.properties(data=[('too', 'many', 'elements')]),
      api.expect_exception('AssertionError'),
      api.post_process(post_process.DropExpectation),
  )
