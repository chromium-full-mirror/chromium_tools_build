# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine.post_process import (DoesNotRunRE, DropExpectation,
                                        SummaryMarkdown)

DEPS = [
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/step',
    'v8',
    'v8_auto_roller',
]


def RunSteps(api):
  """This tests the CL manager using a dummy roller."""

  update_result = api.v8_auto_roller.setup_target(
      'dummy',
      'https://chromium.googlesource.com/dumb/dumber',
  )
  source_dir = update_result.source_root.path

  clm = api.v8_auto_roller.build_cl_manager(
      source_dir, bugs='dummy:123', patched_cl_has_priority=True)

  api.v8_auto_roller.dummy_roll(clm, source_dir)
  return api.v8_auto_roller.report_result()


def GenTests(api):
  yield api.test('skip') + api.override_step_data(
      'Update dummy deps.gerrit changes',
      api.json.output([{
          '_number': '123',
          'subject': 'dummy',
          'current_revision_number': 2,
      }])) + api.post_process(
          DoesNotRunRE,
          api.v8.exclude_by_names_re(
              "Setup",
              "Setup.ensure builder cache dir",
              "Setup.bot_update",
              "Setup.gerrit get_gerrit_branch (chromium/src refs/heads/main)",
              "Setup.fetch deadbeef:DEPS",
              "Setup.ensure chromium cache dir",
              "Setup.Store src/DEPS",
              "Update dummy deps",
              "Update dummy deps.get login info",
              "Update dummy deps.gerrit changes",
              "Update dummy deps.CL 123 was modified.",
              "Update dummy deps.gerrit add message",
              "Update dummy deps.Found existing roll CL. Skipping.",
              "$result",
          )) + api.post_process(DropExpectation)
