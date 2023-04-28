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

from recipe_engine import recipe_api, post_process
from recipe_engine.post_process import DropExpectation

DEPS = [
    'code_coverage',
    'recipe_engine/step',
]


def RunSteps(api):
  with api.step.nest('process fuzz coverage') as step_result:
    try:
      api.code_coverage.get_chromium_fuzz_coverage()
    except api.step.StepFailure:
      step_result.logs['fuzz coverage logs'] = "Could not process fuzz coverage"


def GenTests(api):
  yield api.test(
      "basic", api.post_process(post_process.MustRun, 'process fuzz coverage'))
  yield api.test(
      "failure",
      api.step_data(
          'process fuzz coverage.generate coverage metadata', retcode=1),
  )
