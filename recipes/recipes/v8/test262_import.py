# Copyright 2023 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""
Recipe for importing Test262 changes.
"""

from recipe_engine.post_process import (DropExpectation, MustRun)

DEPS = [
    'recipe_engine/raw_io',
    'v8_auto_roller',
]

CONFIG = {
    "manual_roll_reviewers": [
        "nikolaos@chromium.org", "olivf@chromium.org", "rezvan@chromium.org"
    ],
}


def RunSteps(api):
  update_result = api.v8_auto_roller.setup_target(
      'v8',
      'https://chromium.googlesource.com/v8/v8',
      requires_chromium_checkout=True,
  )
  source_dir = update_result.source_root.path

  clm = api.v8_auto_roller.build_cl_manager(source_dir, bugs="v8:7834")

  api.v8_auto_roller.test262_roll(CONFIG, clm, source_dir)

  return api.v8_auto_roller.report_result()


def GenTests(api):
  yield (
    api.test('default') +
    api.override_step_data(
        'Update test262 import deps.Update Test262 status file.',
        api.raw_io.stream_output_text('123..234', stream='stdout'),
      ) +
    api.post_process(MustRun, 'Update test262 import deps.gerrit changes') +
    api.post_process(DropExpectation)
  )
