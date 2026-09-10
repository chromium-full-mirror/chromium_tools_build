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
  v8_rollers = [
    roller('V8 DEPS Trusted', 'Update V8 DEPS (trusted)'),
    roller('V8 DEPS Reviewed', 'Update V8 DEPS (reviewed)'),
    roller('ICU Trusted', 'Update ICU (trusted)'),
    roller('ICU Reviewed', 'Update ICU (reviewed)'),
    roller('google_benchmark Trusted', 'Update google_benchmark (trusted)'),
    roller('google_benchmark Reviewed', 'Update google_benchmark (reviewed)'),
  ]
  return api.v8_roll_watcher.process_rollers(v8_rollers)


def roller(name, subject):
  return {
    "name": name,
    "subject": subject,
    "account": "v8-ci-autoroll-builder"
    "@chops-service-accounts.iam.gserviceaccount.com",
    "project": "v8/v8",
    "review-host": "chromium-review.googlesource.com",
  }


def GenTests(api: RecipeTestApi):
  # Minimal test for recipe coverage (still redundant with module tests).
  yield api.test('basic') + api.post_process(DropExpectation)
