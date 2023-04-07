# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import DropExpectation, SummaryMarkdown

DEPS = [
  'isolate',
  'recipe_engine/properties',
]


def RunSteps(api):
  api.isolate.check_swarm_hashes(['some_target', 'other_target'])


def GenTests(api):
  yield api.test(
      'matching',
      api.properties(
          swarm_hashes={
              'some_target': 'a' * 40,
              'other_target': 'b' * 40,
              'another_one': 'c' * 40
          }),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'detected',
      api.post_process(DropExpectation),
  )

  yield api.test(
      'missing',
      api.properties(swarm_hashes={
          'some_target': 'a' * 40,
          'another_one': 'c' * 40
      }),
      api.post_process(
          SummaryMarkdown,
          'Missing isolated target(s) other_target in swarm_hashes'),
      api.expect_status('INFRA_FAILURE'),
      api.post_process(DropExpectation),
  )
