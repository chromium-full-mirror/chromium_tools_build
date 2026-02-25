# Copyright 2023 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""
Recipe for rolling incoming changes in DevTools.
"""

from recipe_engine.post_process import (DropExpectation, MustRun)
import json

DEPS = [
    'recipe_engine/file',
    'recipe_engine/path',
    'v8_auto_roller',
]

CONFIG = {
    "subject": "Update DevTools DEPS",
    "manual_roll_reviewers": [
        "chrome-devtools-waterfall-gardener-emea-oncall@google.com",
    ],
    "excludes": [
        "extensions/cxx_debugging/third_party/lldb-eval/src",
        "extensions/cxx_debugging/third_party/llvm/src",
        "third_party/chrome/chrome-win",
        "third_party/chrome/chrome-mac-x64",
        "third_party/chrome/chrome-mac-arm64",
        "third_party/chrome/chrome-linux",
        "third_party/cmake",
        "third_party/esbuild",
        "third_party/rollup_libs",
        "scripts/ai_assistance/suite/outputs",
    ],
    "show_commit_log": False,
}


def RunSteps(api):
  update_result = api.v8_auto_roller.setup_target(
      'devtools-frontend',
      'https://chromium.googlesource.com/devtools/devtools-frontend',
      requires_chromium_checkout=True,
  )
  source_dir = update_result.source_root.path

  clm = api.v8_auto_roller.build_cl_manager(
      source_dir,
      bugs="none",
      patched_cl_has_priority=True,
      cc="chrome-devtools-staff+oncall-change@google.com")

  api.v8_auto_roller.regular_roll(CONFIG, clm, source_dir)
  api.v8_auto_roller.scripted_rolls(CONFIG, clm, source_dir, [
      "puppeteer-core",
      "puppeteer-replay",
      "browser-protocol and CfT",
  ])


  return api.v8_auto_roller.report_result()


def GenTests(api):

  def dummy_deps(*keys):
    return "deps = " + json.dumps({
        dep_name: 'https://googlesource.org/deps/s.git@123' for dep_name in keys
    })

  yield (
      api.test('default') + api.override_step_data(
          'Find updated deps.Read devtools-frontend/DEPS',
          api.file.read_text(dummy_deps(*CONFIG["excludes"])),
      ) + api.path.exists(api.path.cleanup_dir.joinpath('roll_output.json')) +
      api.override_step_data(
          'Scripted rolls.Update Browser Protocol & CfT deps.Read roll output',
          api.file.read_json({
              "old_revision": "123",
              "new_revision": "456"
          })) +
      api.post_process(MustRun, 'Update reviewed deps.gerrit changes') +
      api.post_process(MustRun, 'Update trusted deps.gerrit changes') +
      api.post_process(
          MustRun, 'Scripted rolls.Update Puppeteer Core deps.'
          'Run Puppeteer Core script') + api.post_process(
              MustRun, 'Scripted rolls.Update Puppeteer Replay deps.'
              'Run Puppeteer Replay script') +
      api.post_process(DropExpectation))
