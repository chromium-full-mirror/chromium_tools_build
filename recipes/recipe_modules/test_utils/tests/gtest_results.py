# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import itertools

from recipe_engine import post_process

DEPS = [
    'test_utils',
    'recipe_engine/json',
    'recipe_engine/properties',
    'recipe_engine/step',
]


def RunSteps(api):
  api.step('fake_test',
           ['fake', '--gtest-results', api.test_utils.gtest_results()])


def GenTests(api):
  yield api.test(
      'many-log-lines',
      api.override_step_data(
          'fake_test',
          api.test_utils.gtest_results(
              api.json.dumps({
                  'per_iteration_data': [{
                      'SpammyTest': [{
                          'elapsed_time_ms':
                              1000,
                          'output_snippet':
                              '\n'.join(itertools.repeat('line', 10000)),
                          'status':
                              'SUCCESS',
                      }],
                  }],
              }))),
      api.post_process(post_process.DropExpectation),
  )
