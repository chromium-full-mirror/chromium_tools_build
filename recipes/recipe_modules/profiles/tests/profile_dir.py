# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

DEPS = [
    'profiles',
    'recipe_engine/assertions',
]


def RunSteps(api):

  api.assertions.assertFalse(api.profiles._root_profile_dir)
  api.profiles.profile_dir()

  api.assertions.assertTrue(api.profiles._root_profile_dir)
  api.assertions.assertTrue(api.profiles.profile_dir())

  api.profiles.profile_dir(identifier='random_key')
  api.assertions.assertTrue(api.profiles.profile_subdirs)


def GenTests(api):

  yield api.test(
      'basic',
      api.post_process(post_process.DropExpectation),
  )
