# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import DropExpectation

DEPS = [
    'v8_roll_watcher',
]


def RunSteps(api):
  return api.v8_roll_watcher.process_rollers([{
      "name":"Test262 import watcher",
      "subject":"[test262] Roll test262",
      "account":"v8-ci-autoroll-builder@chops-service-accounts.iam.gserviceaccount.com",
      "project":"v8/v8",
      "review-host":"chromium-review.googlesource.com",
      "criteria": ["-hashtag:test262_status_file_patched"],
      "failure_recovery":"test262_update_status_file"
  }])


def GenTests(api):
  # Minimal test for recipe coverage (still redundant with module tests).
  yield api.test('basic') + api.post_process(DropExpectation)
