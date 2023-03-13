# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import json

from recipe_engine import post_process
from recipe_engine.recipe_api import Property

DEPS = [
    'flaky_reproducer',
    'recipe_engine/step',
    'recipe_engine/json',
    'recipe_engine/properties',
    'recipe_engine/resultdb',
]

PROPERTIES = {
    'task_id': Property(default='54321fffffabc123', kind=str),
}


def RunSteps(api, task_id):
  with api.step.nest('get_test_binary') as presentation:
    test_binary = api.flaky_reproducer.get_test_binary(task_id)
    presentation.logs['test_binary.json'] = api.json.dumps(
        test_binary.to_jsonish(), indent=2).splitlines()


def GenTests(api):
  yield api.test(
      'gtest_from_test_request',
      api.step_data(
          'get_test_binary.get_test_binary from 54321fffffabc123',
          api.json.output_stream(
              json.loads(
                  api.flaky_reproducer.get_test_data(
                      'gtest_task_request.json')))),
      api.post_process(post_process.StepSuccess,
                       'get_test_binary.get_test_binary from 54321fffffabc123'),
      api.post_process(post_process.LogContains,
                       'get_test_binary.get_test_binary from 54321fffffabc123',
                       'json.output', [
                           '"./base_unittests.exe"',
                           '"--test-launcher-bot-mode"',
                       ]),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'blink_web_tests_from_test_request',
      api.step_data(
          'get_test_binary.get_test_binary from 54321fffffabc123',
          api.json.output_stream(
              json.loads(
                  api.flaky_reproducer.get_test_data(
                      'blink_web_tests_task_request.json')))),
      api.post_process(post_process.StepSuccess,
                       'get_test_binary.get_test_binary from 54321fffffabc123'),
      api.post_process(post_process.LogContains,
                       'get_test_binary.get_test_binary from 54321fffffabc123',
                       'json.output', [
                           '"bin/run_blink_wpt_tests"',
                       ]),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  chrome_all_tast_tests_task_request = json.loads(
      api.flaky_reproducer.get_test_data('gtest_task_request.json'))
  chrome_all_tast_tests_task_request['tags'].append(
      'test_suite:chrome_all_tast_tests')

  yield api.test(
      'chrome_all_tast_tests',
      api.step_data('get_test_binary.get_test_binary from 54321fffffabc123',
                    api.json.output_stream(chrome_all_tast_tests_task_request)),
      api.expect_exception(NotImplementedError.__name__),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'gtest_for_android',
      api.step_data(
          'get_test_binary.get_test_binary from 54321fffffabc123',
          api.json.output_stream({
              "task_slices": [{
                  "properties": {
                      "command": [
                          "rdb", "stream", "-test-id-prefix",
                          "ninja://chrome/test:unit_tests/", "-var",
                          "builder:android-12-x64-rel", "-var",
                          "os:Ubuntu-18.04", "-var", "test_suite:unit_tests",
                          "-tag", "step_name:unit_tests", "on", "Ubuntu-18.04",
                          "-tag", "target_platform:android",
                          "-coerce-negative-duration", "-location-tags-file",
                          "../../testing/location_tags.json",
                          "-exonerate-unexpected-pass", "--", "luci-auth",
                          "context", "--", "vpython3",
                          "../../build/android/test_wrapper/logdog_wrapper.py",
                          "--target", "unit_tests", "--logdog-bin-cmd",
                          "../../.task_template_packages/logdog_butler",
                          "--store-tombstones",
                          ("--test-launcher-summary-output="
                           "${ISOLATED_OUTDIR}/output.json"),
                          "--gs-results-bucket=chromium-result-details",
                          "--recover-devices",
                          ("--avd-config=../../tools/android/avd/proto/"
                           "generic_android31.textpb")
                      ]
                  }
              }]
          })),
      api.post_check(post_process.LogContains, 'get_test_binary',
                     'test_binary.json', ['GTestTestBinary']),
      api.post_process(post_process.DropExpectation),
  )

  task_request = json.loads(
      api.flaky_reproducer.get_test_data('gtest_task_request.json'))
  task_request['task_slices'] = []
  yield api.test(
      'from_test_request_without_slice',
      api.step_data('get_test_binary.get_test_binary from 54321fffffabc123',
                    api.json.output_stream(task_request)),
      api.expect_exception(ValueError.__name__),
      api.post_process(
          post_process.SummaryMarkdown, "Uncaught Exception: "
          "ValueError('No TaskSlice found in the TaskRequest.')"),
      api.post_process(post_process.StatusException),
      api.post_process(post_process.DropExpectation),
  )
