# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process
from recipe_engine.post_process import DropExpectation
from RECIPE_MODULES.build.chromium_tests import steps

DEPS = ['code_coverage']


def RunSteps(api):
  api.code_coverage.get_chromium_fuzz_coverage('', '', '', '')


def GenTests(api):
  yield api.test(
      'basic', api.post_process(post_process.MustRun, 'chmod llvm file'),
      api.post_process(post_process.MustRun,
                       'ensure metadata dir for overall tests'),
      api.post_process(post_process.MustRun,
                       'ensure metadata dir for overall tests (2)'),
      api.post_process(post_process.MustRun, 'generate coverage metadata'),
      api.post_process(post_process.MustRun,
                       'gsutil Upload coverage artifacts'),
      api.post_process(DropExpectation))
