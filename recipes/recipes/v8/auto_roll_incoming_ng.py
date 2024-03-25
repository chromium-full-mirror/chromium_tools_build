# Copyright 2023 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""
Recipe for rolling incoming changes in V8.

We control via configurations some dependencies that we want to roll in
separate CLs, thus comming from separate roll bots.
"""

from recipe_engine.post_process import (DropExpectation, MustRun)
from recipe_engine.recipe_api import Property
from recipe_engine.config import ConfigGroup, Dict, Single, List

DEPS = [
    'recipe_engine/properties',
    'v8_auto_roller',
]

PROPERTIES = {
    # Configuration of the auto-roller
    'autoroller_config':
        Property(
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
                # Mapping between the dependency name in the target project and
                # the name in the source project
                deps_key_mapping=Dict(value_type=str),
                # List of reviewers of rolling CLs requiring a manual review
                reviewers=List(str),
                # Add extra log entries to the commit message.
                show_commit_log=Single(bool),
                # List of footers added to the commit message.
                commit_msg_footers=Single(list, empty_val=None),
            )),
}

def RunSteps(api, autoroller_config):
  api.v8_auto_roller.setup_target(
      'v8',
      'https://chromium.googlesource.com/v8/v8',
  )

  clm = api.v8_auto_roller.build_cl_manager()

  api.v8_auto_roller.regular_roll(autoroller_config, clm)

  return api.v8_auto_roller.report_result()


def GenTests(api):
  yield (
    api.test('default') +
    api.properties(autoroller_config={
      "subject" : "Not important",
       "reviewers" : ["ciciobello@chromium.org", "jonnybravo@google.com"],
       "show_commit_log": True,
    }) +
    api.post_process(MustRun, 'Update reviewed deps.gerrit changes') +
    api.post_process(MustRun, 'Update trusted deps.gerrit changes') +
    api.post_process(DropExpectation)
  )
