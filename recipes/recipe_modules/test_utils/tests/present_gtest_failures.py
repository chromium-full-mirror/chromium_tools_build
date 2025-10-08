# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

DEPS = [
    'test_utils',
    'recipe_engine/json',
    'recipe_engine/step',
]


def RunSteps(api):

  step_result = api.step(
      'test', ['test_binary', '--result-json', api.test_utils.gtest_results()],
      ok_ret='all')
  api.test_utils.present_gtest_failures(step_result)


def GenTests(api):
  log = 'Deterministic failure: Test.One (status FAILURE)'
  notrun_log = 'Deterministic failure: Test.Two (status NOTRUN)'
  flaky_log = 'Flaky failure: Test.One (status FAILURE,SUCCESS)'
  success_log_keys = ['test_utils.gtest_results']

  yield api.test(
      'failure',
      api.override_step_data(
          'test',
          api.test_utils.gtest_results(
              api.json.dumps({
                  'per_iteration_data': [{
                      'Test.One': [{
                          'elapsed_time_ms': 0,
                          'output_snippet': ':(',
                          'status': 'FAILURE',
                      },]
                  }],
              })),
          retcode=1),
      api.post_check(lambda check, steps: check(log in steps['test'].logs)),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'failures_with_notrun',
      api.override_step_data(
          'test',
          api.test_utils.gtest_results(
              api.json.dumps({
                  'per_iteration_data': [{
                      'Test.One': [{
                          'elapsed_time_ms': 0,
                          'output_snippet': ':(',
                          'status': 'FAILURE',
                      },],
                      'Test.Two': [{
                          'elapsed_time_ms': 0,
                          'output_snippet': ':(',
                          'status': 'NOTRUN',
                      },]
                  }],
              })),
          retcode=1),
      api.post_check(lambda check, steps: check(log in steps['test'].logs)),
      api.post_check(lambda check, steps: check(notrun_log not in steps['test'].
                                                logs)),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'notrun',
      api.override_step_data(
          'test',
          api.test_utils.gtest_results(
              api.json.dumps({
                  'per_iteration_data': [{
                      'Test.Two': [{
                          'elapsed_time_ms': 0,
                          'output_snippet': ':(',
                          'status': 'NOTRUN',
                      },]
                  }],
              })),
          retcode=1),
      api.post_check(lambda check, steps: check(notrun_log in steps['test'].logs
                                               )),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'flake',
      api.override_step_data(
          'test',
          api.test_utils.gtest_results(
              api.json.dumps({
                  'per_iteration_data': [{
                      'Test.One': [
                          {
                              'elapsed_time_ms': 0,
                              'output_snippet': ':(',
                              'status': 'FAILURE',
                          },
                          {
                              'elapsed_time_ms': 0,
                              'output_snippet': ':)',
                              'status': 'SUCCESS',
                          },
                      ]
                  }],
              })),
          retcode=1),
      api.post_check(lambda check, steps: check(flaky_log in steps['test'].logs)
                    ),
      api.post_process(post_process.DropExpectation),
  )

  def check_step_has_logs(check, steps, step, expected_logs):
    logs = list(steps[step].logs)
    check(logs == expected_logs)

  yield api.test(
      'flake_skipped',
      api.override_step_data(
          'test',
          api.test_utils.gtest_results(
              api.json.dumps({
                  'per_iteration_data': [{
                      'Test.One': [
                          {
                              'elapsed_time_ms': 0,
                              'output_snippet': ':(',
                              'status': 'FAILURE',
                          },
                          {
                              'elapsed_time_ms': 0,
                              'output_snippet': ':)',
                              'status': 'SKIPPED',
                          },
                      ]
                  }],
              })),
          retcode=1),
      api.post_check(check_step_has_logs, 'test', success_log_keys),
      api.post_process(post_process.DropExpectation),
  )
