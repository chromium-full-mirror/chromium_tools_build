# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Recipe used to launch test runner using the rr tool.

The recipe will compile input tests, and execute test runner script to run tests
using the rr tool, and upload the recorded traces to GCS.
"""
from recipe_engine.post_process import DropExpectation
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipes.build.chromium_rr.test_launcher import InputProperties

PROPERTIES = InputProperties

DEPS = [
    'builder_group',
    'chromium',
    'chromium_checkout',
    'chromium_polymorphic',
    'chromium_tests',
    'chromium_tests_builder_config',
    'depot_tools/gclient',
    'recipe_engine/context',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/step',
]


def RunSteps(api, properties):

  test_suites = []
  for test_info in properties.target_test_infos:
    if test_info.test_suite:
      test_suites.append(test_info.test_suite)

  if not test_suites:
    return

  builder_id, builder_config = api.chromium_polymorphic.lookup_builder_config()
  api.chromium_tests.configure_build(builder_config)
  update_step, _, _ = api.chromium_tests.prepare_checkout(
      builder_config, report_cache_state=False)

  api.chromium.output_dir = update_step.source_root.path.joinpath(
      'out', api.chromium.c.build_config_fs)
  source_dir = update_step.source_root.path
  build_dir = api.chromium.default_build_dir(source_dir)
  # TODO(jiesheng): Override the symbol level to 2 here.
  raw_result = api.chromium_tests.run_mb_and_compile(
      build_dir, builder_id, test_suites, isolated_targets=[], name_suffix='')
  if raw_result.status != common_pb.SUCCESS:
    return raw_result

  # TODO(jiesheng): Run the tests using the test runner script using the
  # compiled tests.


def GenTests(api):
  ctbc_api = api.chromium_tests_builder_config

  yield api.test(
      'happy_path_compile_test',
      api.chromium_polymorphic.triggered_properties(
          project='fake-project',
          bucket='fake-bucket',
          builder='fake-builder',
          builder_group='fake-group',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder='fake-builder',
              builder_group='fake-group',
          ).assemble()),
      api.properties(
          InputProperties(
              target_test_infos=[
                  InputProperties.TestInfo(
                      test_suite='blink_wpt_tests',
                      test_names=['test1', 'test2'],
                  ),
              ],)),
      api.builder_group.for_current('chromium.fyi'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'no_input',
      api.chromium_polymorphic.triggered_properties(
          project='fake-project',
          bucket='fake-bucket',
          builder='fake-builder',
          builder_group='fake-group',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder='fake-builder',
              builder_group='fake-group',
          ).assemble()),
      api.builder_group.for_current('chromium.fyi'),
      api.expect_status('SUCCESS'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'compile_with_failure',
      api.chromium_polymorphic.triggered_properties(
          project='fake-project',
          bucket='fake-bucket',
          builder='fake-builder',
          builder_group='fake-group',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder='fake-builder',
              builder_group='fake-group',
          ).assemble()),
      api.properties(
          InputProperties(
              target_test_infos=[
                  InputProperties.TestInfo(
                      test_suite='blink_wpt_tests',
                      test_names=['test1', 'test2'],
                  ),
              ],)),
      api.builder_group.for_current('chromium.fyi'),
      api.step_data('compile', retcode=1),
      api.expect_status('FAILURE'),
      api.post_process(DropExpectation),
  )
