# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Placeholder recipe for the Chromium fuzzing coverage builder.

As of right now, this recipe only runs `ls` and exits.

Once the "setting up a new builder" configuration is complete end-to-end and
verified to work, this recipe will be expanded to support:

(1) build all Chrome fuzzers
(2) download the fuzzing corpora from Clusterfuzz
(3) run all fuzzers with those corpora, and
(4) output the results in the form of code coverage information,
(5) to be displayed on the Chrome coverage dashboard.
"""

DEPS = [
    "recipe_engine/step",
]


def RunSteps(api):
  api.step("list directory contents", ["ls"])


def GenTests(api):
  yield api.test("basic")
