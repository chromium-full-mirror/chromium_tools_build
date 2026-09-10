# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import DropExpectation

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import v8_roll_watcher


@dataclass
class DEPS(RecipeScriptApi):
  v8_roll_watcher: v8_roll_watcher.API


def RunSteps(api: DEPS):
  return api.v8_roll_watcher.process_rollers(
    [
      {
        "name": "Test262 import watcher",
        "subject": "[test262] Roll test262",
        "account": "v8-ci-autoroll-builder@chops-service-accounts.iam.gserviceaccount.com",
        "project": "v8/v8",
        "review-host": "chromium-review.googlesource.com",
        "criteria": ["-hashtag:test262_status_file_patched"],
        "failure_recovery": ["test262_update_status_file"],
      }
    ]
  )


def GenTests(api: RecipeTestApi):
  # Minimal test for recipe coverage (still redundant with module tests).
  yield api.test('basic') + api.post_process(DropExpectation)
