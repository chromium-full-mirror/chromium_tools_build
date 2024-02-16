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
    "subject": "Update DevTools DEPS",
    "excludes": [
        # `esbuild` is manually rolled; Chromium pulls our version from
        # devtools-frontend.
        "third_party/esbuild:infra/3pp/tools/esbuild/${platform}",
    ],
    "reviewers": [
        "devtools-waterfall-sheriff-onduty@rotations.google.com",
    ],
    "show_commit_log": False,
}


def RunSteps(api):
  api.v8_auto_roller.setup_target(
      'devtools-frontend',
      'https://chromium.googlesource.com/devtools/devtools-frontend',
  )

  clm = api.v8_auto_roller.build_cl_manager(bugs="none")

  api.v8_auto_roller.regular_roll(CONFIG, clm)
  api.v8_auto_roller.cft_pin_roll(CONFIG, clm)
  api.v8_auto_roller.scripted_rolls(CONFIG, clm, [
      "puppeteer-core",
      "puppeteer-replay",
      "browser-protocol",
  ])


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
