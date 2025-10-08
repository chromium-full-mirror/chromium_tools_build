# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from RECIPE_MODULES.build.chromium_types import BuilderId

DEPS = ['recipe_engine/assertions']


def RunSteps(api):
  builder_id = BuilderId.create_for_group('fake-group', 'fake-builder')
  api.assertions.assertEqual(str(builder_id), 'fake-group:fake-builder')


def GenTests(api):
  yield api.test(
      'full',
      api.post_process(post_process.DropExpectation),
  )
