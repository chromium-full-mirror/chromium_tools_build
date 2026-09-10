# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium_gerrit_utils
from RECIPE_MODULES.depot_tools import gerrit


@dataclass
class DEPS(RecipeScriptApi):
  chromium_gerrit_utils: chromium_gerrit_utils.API
  gerrit: gerrit.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  gerrit: gerrit.TEST_API


def RunSteps(api: DEPS):
  api.chromium_gerrit_utils.create_temp_cl('some/file/path', 'some-topic')
  api.chromium_gerrit_utils.abandon_old_cls('some-topic', '24h')


def GenTests(api: TEST_DEPS):
  yield api.test(
    'basic',
    api.override_step_data(
      'gerrit create change at (chromium/src main)',
      api.gerrit.update_files_response_data(change_number=123456),
    ),
    api.step_data(
      'abandon old CLs.gerrit changes',
      api.gerrit.get_one_change_response_data(change_number=654321),
    ),
    api.post_check(
      post_process.MustRun, 'abandon old CLs.gerrit abandon 654321'
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'abandon_old_cls_failure',
    api.override_step_data(
      'gerrit create change at (chromium/src main)',
      api.gerrit.update_files_response_data(change_number=123456),
    ),
    api.step_data(
      'abandon old CLs.gerrit changes',
      api.gerrit.get_one_change_response_data(change_number=654321),
    ),
    # A failure to abandon an old CL shouldn't fail the build.
    api.step_data('abandon old CLs.gerrit abandon 654321', retcode=1),
    api.post_process(post_process.DropExpectation),
  )
