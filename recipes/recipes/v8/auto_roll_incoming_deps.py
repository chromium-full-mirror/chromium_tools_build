# Copyright 2023 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""
Recipe for rolling incoming changes in V8.

We control via configurations some dependencies that we want to roll in
separate CLs, thus comming from separate roll bots.
"""

from recipe_engine.post_process import DropExpectation, MustRun
from recipe_engine.recipe_api import Property
from recipe_engine.config import ConfigGroup, Dict, Single, List

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import v8_auto_roller
from RECIPE_MODULES.recipe_engine import properties


@dataclass
class DEPS(RecipeScriptApi):
  properties: properties.API
  v8_auto_roller: v8_auto_roller.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  properties: properties.TEST_API


PROPERTIES = {
  # Configuration of the auto-roller
  'autoroller_config': Property(
    kind=ConfigGroup(
      # Subject of the rolling CLs; (trusted) or (reviewed) is
      # appended
      subject=Single(str),
      # List of (target side) dependencies to be excluded from rolling
      # with the current config
      excludes=Single(list, empty_val=None),
      # List of (target side) dependencies to be included when rolling
      # with the current config
      includes=Single(list, empty_val=None),
      # List of reviewers of rolling CLs requiring a manual review
      manual_roll_reviewers=List(str),
      # Add extra log entries to the commit message.
      show_commit_log=Single(bool),
      # List of footers added to the commit message.
      commit_msg_footers=Single(list, empty_val=None),
    )
  ),
}


def RunSteps(api: DEPS, autoroller_config):
  update_result = api.v8_auto_roller.setup_target(
    'v8',
    'https://chromium.googlesource.com/v8/v8',
  )
  source_dir = update_result.source_root.path

  clm = api.v8_auto_roller.build_cl_manager(source_dir)

  api.v8_auto_roller.regular_roll(autoroller_config, clm, source_dir)

  return api.v8_auto_roller.report_result()


def GenTests(api: TEST_DEPS):
  yield (
    api.test('default')
    + api.properties(
      autoroller_config={
        "subject": "Not important",
        "manual_roll_reviewers": [
          "ciciobello@chromium.org",
          "jonnybravo@google.com",
        ],
        "show_commit_log": True,
      }
    )
    + api.post_process(MustRun, 'Update reviewed deps.gerrit changes')
    + api.post_process(MustRun, 'Update trusted deps.gerrit changes')
    + api.post_process(DropExpectation)
  )
