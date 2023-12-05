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
    "target_config": {
        "solution_name": "v8",
        "project_name": "v8/v8",
        "account": "v8-ci-test262-import-export@chops-service-accounts.iam.gserviceaccount.com",
    },
    "subject": "[test262] Roll test262",
    "roll_test262": True,
    "regular_deps_roller": False,
    "reviewers": [
        "syg@chromium.org",
    ],
    "bugs": "v8:7834",
}


def RunSteps(api):
  api.v8_auto_roller.setup(CONFIG)

  api.v8_auto_roller.test262_roll(CONFIG)

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
