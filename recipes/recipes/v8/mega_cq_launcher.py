# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""A small orch-like recipe used to launch builds in V8's "Mega" CQ

This recipe can be removed once Chromium's mega-cq launcher recipe is
migrated to CV.
"""

from recipe_engine import post_process

DEPS = [
    'chromium_mega_cq',
    'recipe_engine/buildbucket',
]


def RunSteps(api):
  # TODO(https://crbug.com/336441276): Read list of trybots from V8.
  trybots = ['v8_linux_noi18n_rel']

  return api.chromium_mega_cq.trigger_and_collect_bots(trybots)


def GenTests(api):

  yield api.test(
      'basic',
      api.buildbucket.try_build(),
      api.post_process(post_process.DropExpectation),
  )