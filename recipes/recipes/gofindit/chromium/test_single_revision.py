# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import (DropExpectation, MustRun)

DEPS = ['recipe_engine/step']


# Run a specific test for a particular revision.
def RunSteps(api):
  api.step('run_test', ['echo', 'Run Test'])


def GenTests(api):
  yield api.test(
      'should_run_test',
      api.post_process(MustRun, 'run_test'),
      api.post_process(DropExpectation),
  )
