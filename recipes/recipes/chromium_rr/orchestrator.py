# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Recipe used to launch CI build with the rr tool.

The recipe will select flaky tests from last 10 days and trigger rr test
launcher recipe to run and record tests.
"""

import collections
from google.protobuf import json_format

from PB.go.chromium.org.luci.buildbucket.proto \
    import builder_common as builder_common_pb
from PB.recipes.build.chromium_rr.test_launcher \
    import InputProperties
from recipe_engine.post_process import DropExpectation

DEPS = [
    'builder_group',
    'chromium',
    'chromium_polymorphic',
    'recipe_engine/buildbucket',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/led',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/step',
]

TEST_SUITE_ALLOW_LIST = [
    'blink_wpt_tests', 'blink_web_tests',
    'not_site_per_process_blink_wpt_tests',
    'not_site_per_process_blink_web_tests', 'high_dpi_blink_wpt_tests',
    'high_dpi_blink_web_tests'
]
MAX_TESTS_PER_BUG = 3


def RunSteps(api):
  # Select tests from past 10 days' bugs.
  cmd = [
      'vpython3',
      api.resource('test_selection.py'), '--output-json',
      api.json.output(), '--sample-day=10'
  ]
  step_result = api.step('query test data', cmd)
  query_results = step_result.json.output

  builder_to_tests = collections.defaultdict(
      lambda: collections.defaultdict(list))
  bug_to_test_num = collections.defaultdict(int)
  for query_result in query_results:
    # TODO(jiesheng): Support other test type for rr test launcher.
    test_suite = query_result.get('test_suite', '')
    if test_suite not in TEST_SUITE_ALLOW_LIST:
      continue
    builder = query_result.get('builder', '')
    test_name = query_result.get('test_name', '')
    bug_id = query_result.get('bug_id', '')
    if (not builder or not test_name or not bug_id or
        bug_to_test_num[bug_id] > MAX_TESTS_PER_BUG):
      continue
    builder_to_tests[builder][test_suite].append(test_name)
    bug_to_test_num[bug_id] += 1

  for builder, test_suites in builder_to_tests.items():
    # Use hard coded chromium and ci here which is same as the values in
    # test_selection.sql.
    builder_id = builder_common_pb.BuilderID(
        project='chromium', bucket='ci', builder=builder)
    properties = api.chromium_polymorphic.get_target_properties(builder_id)

    target_test_infos = InputProperties()
    for test_suite, test_names in test_suites.items():
      target_test_info = target_test_infos.target_test_infos.add()
      target_test_info.test_suite = test_suite
      target_test_info.test_names.extend(test_names)
    properties.update(
        json_format.MessageToDict(
            target_test_infos, preserving_proto_field_name=True))

    if api.led.launched_by_led:
      api.led.trigger_builder(
          api.buildbucket.build.builder.project,
          api.led.shadowed_bucket,
          'linux-rr-test-launcher-fyi',
          properties,
          use_payload=True)
    else:
      api.buildbucket.schedule(
          [
              api.buildbucket.schedule_request(
                  project=api.buildbucket.build.builder.project,
                  bucket=api.buildbucket.build.builder.bucket,
                  builder='linux-rr-test-launcher-fyi',
                  tags=api.buildbucket.build.tags,
                  properties=properties,
                  can_outlive_parent=True,
                  as_shadow_if_parent_is_led=True,
              ),
          ],
          include_sub_invs=False,
      )

def GenTests(api):
  yield api.test(
      'happy_path',
      api.builder_group.for_current('chromium.fyi'),
      api.chromium_polymorphic.properties_on_target_build({
          'builder_group': 'fake-group',
          '$bootstrap/properties': 'fake-bootstrap-properties',
      }),
      api.override_step_data(
          'query test data',
          api.json.output([{
              'test_suite': 'blink_wpt_tests',
              'builder': 'builder1',
              'test_name': 'test_name_123',
              'bug_id': 'bug_123'
          }, {
              'test_suite': 'blink_wpt_tests',
              'builder': 'builder1',
              'test_name': 'test_name_345',
              'bug_id': 'bug_123'
          }, {
              'test_suite': 'blink_web_tests',
              'builder': 'builder1',
              'test_name': 'test_name_567',
              'bug_id': 'bug_123'
          }])),
      api.post_process(DropExpectation),
  )
  yield api.test(
      'happy_path_led',
      api.builder_group.for_current('chromium.fyi'),
      api.properties(
          **{
              "$recipe_engine/led": {
                  "rbe_cas_input": {
                      "digest": {
                          "hash": "123",
                          "size_bytes": 123
                      }
                  },
                  "shadowed_bucket": "ci",
                  "led_run_id": ""
              },
          }),
      api.chromium_polymorphic.properties_on_target_build({
          'builder_group': 'fake-group',
          '$bootstrap/properties': 'fake-bootstrap-properties',
      }),
      api.override_step_data(
          'query test data',
          api.json.output([{
              'test_suite': 'blink_wpt_tests',
              'builder': 'builder1',
              'test_name': 'test_name_123',
              'bug_id': 'bug_123'
          }, {
              'test_suite': 'blink_wpt_tests',
              'builder': 'builder1',
              'test_name': 'test_name_345',
              'bug_id': 'bug_123'
          }, {
              'test_suite': 'blink_web_tests',
              'builder': 'builder1',
              'test_name': 'test_name_567',
              'bug_id': 'bug_123'
          }])),
      api.post_process(DropExpectation),
  )
  yield api.test(
      'no_test_name',
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
              'test_name': 'test_name_234',
              'bug_id': 'bug_123'
          }])),
      api.post_process(DropExpectation),
  )
