# Copyright 2023 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""
Recipe for importing Test262 changes.
"""

from recipe_engine.post_process import (DropExpectation, MustRun)

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import v8_auto_roller
from RECIPE_MODULES.recipe_engine import raw_io


@dataclass
class DEPS(RecipeScriptApi):
  raw_io: raw_io.API
  v8_auto_roller: v8_auto_roller.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  raw_io: raw_io.TEST_API

CONFIG = {
    "manual_roll_reviewers": [
        "nikolaos@chromium.org", "olivf@chromium.org", "rezvan@chromium.org"
    ],
}


def RunSteps(api: DEPS):
  update_result = api.v8_auto_roller.setup_target(
      'v8',
      'https://chromium.googlesource.com/v8/v8',
      requires_chromium_checkout=True,
  )
  source_dir = update_result.source_root.path

  clm = api.v8_auto_roller.build_cl_manager(source_dir, bugs="v8:7834")

  api.v8_auto_roller.test262_roll(CONFIG, clm, source_dir)

  return api.v8_auto_roller.report_result()


def GenTests(api: TEST_DEPS):
  yield (
    api.test('default') +
    api.override_step_data(
        'Update test262 import deps.Update Test262 status file.',
        api.raw_io.stream_output_text('123..234', stream='stdout'),
      ) +
    api.post_process(MustRun, 'Update test262 import deps.gerrit changes') +
    api.post_process(DropExpectation)
  )
