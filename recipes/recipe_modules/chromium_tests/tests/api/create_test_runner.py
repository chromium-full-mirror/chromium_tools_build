# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import (DoesNotRun, DropExpectation, MustRun,
                                        SummaryMarkdown)
from RECIPE_MODULES.build.chromium_tests import steps

DEPS = [
    'builder_group',
    'chromium',
    'chromium_tests',
    'depot_tools/tryserver',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
]


def RunSteps(api):
  api.chromium.set_config('chromium')
  api.chromium.set_build_properties({
      'got_webrtc_revision': 'webrtc_sha',
      'got_v8_revision': 'v8_sha',
      'got_revision': 'd3adv3ggie',
      'got_revision_cp': 'refs/heads/main@{#54321}',
  })

  test_runner = api.chromium_tests.create_test_runner(
      tests=[
          steps.LocalGTestTestSpec.create('base_unittests').get_test(
              api.chromium_tests),
          steps.LocalGTestTestSpec.create('net_unittests').get_test(
              api.chromium_tests),
      ],
      serialize_tests=api.properties.get('serialize_tests'),
      retry_failed_shards=api.properties.get('retry_failed_shards'),
      surface_invalid_results_as_infra_failure=api.properties.get(
          'surface_invalid_results_as_infra_failure'))
  return test_runner()


def GenTests(api):
  yield api.test(
      'failure',
      api.chromium.ci_build(
          builder_group='test_group',
          builder='test_buildername',
      ),
      api.builder_group.for_current('test_group'),
      api.properties(
          buildername='test_buildername', bot_id='test_bot_id',
          buildnumber=123),
      api.override_step_data(
          'base_unittests',
          retcode=1,
          stderr=api.raw_io.output_text(
              'rdb-stream: included "invocations/test-inv" in "build-inv"')),
      api.post_process(DoesNotRun, 'test_pre_run (2)'),
      api.post_process(SummaryMarkdown,
                       '1 Test Suite(s) failed.\n\n**base_unittests** failed.'),
      api.expect_status('FAILURE'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'infra_failure',
      api.chromium.ci_build(
          builder_group='test_group',
          builder='test_buildername',
      ),
      api.builder_group.for_current('test_group'),
      api.properties(
          buildername='test_buildername', bot_id='test_bot_id',
          buildnumber=123,
          surface_invalid_results_as_infra_failure=True),
      api.override_step_data(
          'base_unittests',
          retcode=1,
          stderr=api.raw_io.output_text(
              'rdb-stream: included "invocations/test-inv" in "build-inv"')),
      api.post_process(DoesNotRun, 'test_pre_run (2)'),
      api.post_process(SummaryMarkdown,
                       '1 Test Suite(s) failed.\n\n**base_unittests** failed.'),
      api.expect_status('INFRA_FAILURE'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'serialize_tests',
      api.chromium.ci_build(
          builder_group='test_group',
          builder='test_buildername',
      ),
      api.builder_group.for_current('test_group'),
      api.properties(
          buildername='test_buildername',
          bot_id='test_bot_id',
          buildnumber=123,
          serialize_tests=True),
      api.override_step_data(
          'base_unittests',
          retcode=1,
          stderr=api.raw_io.output_text(
              'rdb-stream: included "invocations/test-inv" in "build-inv"')),
      api.post_process(MustRun, 'test_pre_run (2)'),
      api.post_process(SummaryMarkdown,
                       '1 Test Suite(s) failed.\n\n**base_unittests** failed.'),
      api.expect_status('FAILURE'),
      api.post_process(DropExpectation),
  )
