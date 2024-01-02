# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import DropExpectation

DEPS = [
    'v8_roll_watcher',
]


def RunSteps(api):
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
      "name":
          name,
      "subject":
          subject,
      "account":
          "v8-ci-autoroll-builder"
          "@chops-service-accounts.iam.gserviceaccount.com",
      "project":
          "v8/v8",
      "review-host":
          "chromium-review.googlesource.com",
  }


def GenTests(api):
  # Minimal test for recipe coverage (still redundant with module tests).
  yield api.test('basic') + api.post_process(DropExpectation)
