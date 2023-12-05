# Copyright 2023 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""
Recipe for rolling incoming changes in DevTools.
"""

from recipe_engine.post_process import (DropExpectation, MustRun)

DEPS = [
    'v8_auto_roller',
]


CONFIG = {
    "target_config": {
        "solution_name": "devtools-frontend",
        "project_name": "devtools/devtools-frontend",
        "account": "devtools-ci-autoroll-builder@chops-service-accounts.iam.gserviceaccount.com",
        "log_template": "Rolling %s: %s/+log/%s..%s",
        "cipd_log_template": "Rolling %s: %s..%s",
    },
    "subject": "Update DevTools DEPS",
    "excludes": [
        # `esbuild` is manually rolled; Chromium pulls our version from
        # devtools-frontend.
        "third_party/esbuild:infra/3pp/tools/esbuild/${platform}",
    ],
    "reviewers": [
        "devtools-waterfall-sheriff-onduty@grotations.appspotmail.com",
    ],
    "show_commit_log": False,
    "roll_chromium_pin": True,
    "scripted_rolls": [
        "puppeteer-core",
        "puppeteer-replay",
    ],
    # "Bug: none" is required to pass presubmit tests
    "bugs": "none",
}


def RunSteps(api):
  api.v8_auto_roller.setup(CONFIG)

  api.v8_auto_roller.regular_roll(CONFIG)
  api.v8_auto_roller.cft_pin_roll(CONFIG)
  api.v8_auto_roller.scripted_rolls(CONFIG)

  return api.v8_auto_roller.report_result()


def GenTests(api):
  yield (
    api.test('default') +
    api.post_process(MustRun, 'Update reviewed deps.gerrit changes') +
    api.post_process(MustRun, 'Update trusted deps.gerrit changes') +
    api.post_process(MustRun, 'Update chromium pin deps.gclient get '
                     'chrome deps') +
    api.post_process(MustRun, 'Scripted rolls.Update Puppeteer Core deps.'
                     'Run Puppeteer Core script') +
    api.post_process(MustRun, 'Scripted rolls.Update Puppeteer Replay deps.'
                     'Run Puppeteer Replay script') +
    api.post_process(DropExpectation)
  )
