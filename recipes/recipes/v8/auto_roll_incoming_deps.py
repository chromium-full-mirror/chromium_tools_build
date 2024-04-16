# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import (DoesNotRun, DoesNotRunRE,
                                        DropExpectation, MustRun,
                                        SummaryMarkdown)
from recipe_engine.recipe_api import Property
from recipe_engine.config import ConfigGroup, Dict, Single, List

DEPS = [
    'recipe_engine/buildbucket',
    'recipe_engine/cipd',
    'recipe_engine/json',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
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
                # Configuration parameters of the project where dependencies
                # will be rolled in. The source is always Chromium project.
                target_config=ConfigGroup(
                    # Solution name to be used for project checkout
                    solution_name=Single(str, required=True),
                    # Project name. Together with the 'base_url' it will form
                    # the location for the project
                    project_name=Single(str, required=True),
                    # The name of the account used to create the roll CL
                    account=Single(str),
                    # Template for the commit message used with regular
                    # dependencies
                    log_template=Single(str),
                    # Template for the commit message used with cipd
                    # dependencies
                    cipd_log_template=Single(str),
                    # Gerrit URL to be used for rolling CL review
                    gerrit_base_url=Single(str),
                    # Repo base URL together with 'project_name' to locate the
                    # repo where the rolling CL will be landed
                    base_url=Single(str),
                ),
                # List of (target side) dependencies to be excluded from rolling
                # with the current config
                excludes=Single(list, empty_val=None),
                # List of (target side) dependencies to be included when rolling
                # with the current config
                includes=Single(list, empty_val=None),
                # Specify the source to determine the next version, must be
                # `chromium`, `cipd`, `tip_of_tree`, or `auto` (default). E.g.
                #     ...
                #     "dependency_version_sources": {
                #         "devtools-frontend": "tip-of-tree"
                #     },
                #     ...
                dependency_version_sources=Dict(value_type=str),
                # Flag for rolling trusted and untrusted deps
                regular_deps_roller=Single(
                    bool, empty_val=True, required=False),
                # List of reviewers of rolling CLs requiring a manual review
                reviewers=List(str),
                # Flag for rolling the binary chromium pin in target project
                roll_chromium_pin=Single(bool, empty_val=False, required=False),
                # Flag for rolling the test262
                roll_test262=Single(bool, empty_val=False, required=False),
                # List of keys of supported script assisted rolls
                scripted_rolls=Single(list, empty_val=None),
                # Add extra log entries to the commit message.
                show_commit_log=Single(bool),
                # Bugs included in roll CL description
                bugs=Single(str),
            )),
}

RETSAM = 'retsam'[::-1]
BASE_URL = 'https://chromium.googlesource.com/'


def setup(api, autoroller_config):
  target_config = autoroller_config['target_config']
  solution_name = target_config['solution_name']
  base_url = target_config.get('base_url', BASE_URL)
  target_url = base_url + target_config['project_name']
  api.v8_auto_roller.setup_target(solution_name, target_url)


def RunSteps(api, autoroller_config):
  setup(api, autoroller_config)

  clm = api.v8_auto_roller.build_cl_manager(autoroller_config.get('bugs'))

  api.v8_auto_roller.regular_roll(autoroller_config, clm)

  return api.v8_auto_roller.report_result()


def GenTests(api):
  target_config_v8 = {
    'solution_name': 'v8',
    'project_name': 'v8/v8',
  }
  yield (api.test('default') + api.properties(
      autoroller_config={
          "target_config": target_config_v8,
          "subject": "Not important",
          "reviewers": ["ciciobello@chromium.org", "jonnybravo@google.com"],
          "show_commit_log": True,
      }) + api.post_process(MustRun, 'Update reviewed deps.gerrit changes') +
         api.post_process(MustRun, 'Update trusted deps.gerrit changes') +
         api.post_process(DropExpectation))
