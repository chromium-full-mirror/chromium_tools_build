# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Recipe used to launch CI build with the rr tool.

The recipe will select flaky tests from last 10 days and trigger rr test
launcher recipe to run and record tests.
"""

from recipe_engine.post_process import DropExpectation

DEPS = [
    'builder_group',
    'chromium',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/step',
]

TEST_SUITE_ALLOW_LIST = [
    'blink_wpt_tests', 'blink_web_tests',
    'not_site_per_process_blink_wpt_tests',
    'not_site_per_process_blink_web_tests', 'high_dpi_blink_wpt_tests',
    'high_dpi_blink_web_tests'
]


def RunSteps(api):
  # Select tests from past 10 days' bugs.
  cmd = [
      'vpython3',
      api.resource('test_selection.py'), '--output-json',
      api.json.output(), '--sample-day=10'
  ]
  step_result = api.step('query test data', cmd)
  query_results = step_result.json.output

  builder_to_tests = {}
  for query_result in query_results:
    # TODO(jiesheng): Support other test type for rr test launcher.
    test_suite = query_result.get('test_suite', '')
    if test_suite not in TEST_SUITE_ALLOW_LIST:
      continue
    builder = query_result.get('builder', '')
    test_id = query_result.get('test_id', '')
    if not builder or not test_id:
      continue
    builder_to_tests.setdefault(builder, []).append((test_suite, test_id))

  # TODO(jiesheng): Use chromium_polymorphic to launch the child builders here.


def GenTests(api):
  yield api.test(
      'happy_path',
      api.builder_group.for_current('chromium.fyi'),
      api.override_step_data(
          'query test data',
          api.json.output([{
              'test_suite': 'blink_wpt_tests',
              'builder': 'builder1',
              'test_id': 'test_id_123'
          }, {
              'test_suite': 'blink_wpt_tests',
              'builder': 'builder2',
              'test_id': 'test_id_234'
          }])),
      api.post_process(DropExpectation),
  )
  yield api.test(
      'no_test_id',
      api.builder_group.for_current('chromium.fyi'),
      api.override_step_data(
          'query test data',
          api.json.output([{
              'test_suite': 'blink_wpt_tests',
              'builder': 'builder1',
          }])),
      api.post_process(DropExpectation),
  )
  yield api.test(
      'wrong_test_suite',
      api.builder_group.for_current('chromium.fyi'),
      api.override_step_data(
          'query test data',
          api.json.output([{
              'test_suite': 'non_blink_wpt_tests',
              'builder': 'builder1',
              'test_id': 'test_id_234'
          }])),
      api.post_process(DropExpectation),
  )
