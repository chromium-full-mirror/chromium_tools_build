# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

DEPS = [
    'chromium',
    'depot_tools/tryserver',
    'recipe_engine/buildbucket',
    'recipe_engine/cv',
]

from recipe_engine import post_process


def RunSteps(api):
  api.tryserver.require_is_tryserver()

  api.buildbucket.hide_current_build_in_gerrit()
  api.cv.set_do_not_retry_build()
  api.cv.allow_reuse_for(api.cv.DRY_RUN, api.cv.FULL_RUN)


def GenTests(api):
  yield api.test(
      'basic',
      api.chromium.try_build(
          builder_group='fake-try-group', builder='fake-try-builder'),
      api.post_process(post_process.DropExpectation))
