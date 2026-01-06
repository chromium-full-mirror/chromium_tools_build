# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import attr
import base64

from google.protobuf import duration_pb2
from recipe_engine import post_process
from RECIPE_MODULES.build.chromium_tests import steps
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

from PB.go.chromium.org.luci.buildbucket.proto import build as build_pb2
from PB.go.chromium.org.luci.buildbucket.proto \
  import builder_common as builder_common_pb2
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb2
from PB.go.chromium.org.luci.resultdb.proto.v1 \
    import common as resultdb_common
from PB.go.chromium.org.luci.resultdb.proto.v1 \
    import test_result as test_result_pb2
from PB.go.chromium.org.luci.analysis.proto.v1 import test_history
from PB.go.chromium.org.luci.analysis.proto.v1 import test_verdict

DEPS = [
    'chromium',
    'chromium_swarming',
    'chromium_tests',
    'chromium_tests_builder_config',
    'filter',
    'flakiness',
    'test_utils',
    'depot_tools/gclient',
    'depot_tools/tryserver',
    'recipe_engine/assertions',
    'recipe_engine/buildbucket',
    'recipe_engine/json',
    'recipe_engine/luci_analysis',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/resultdb',
    'recipe_engine/step',
]


def RunSteps(api):
  api.assertions.assertEqual(api.flakiness.gs_bucket, 'flake_endorser')
  api.assertions.assertEqual(
      api.flakiness.gs_source_template(experimental=True).format(
          'project', 'bucket', 'builder', 'latest'),
      'experimental/project/bucket/builder/latest/')

  builder_id, builder_config = (
      api.chromium_tests_builder_config.lookup_builder())
  with api.chromium.chromium_layout():
    return api.chromium_tests.trybot_steps(builder_id, builder_config)


def GenTests(api):
  builder = builder_common_pb2.BuilderID(
      builder='fake-try-builder', project='chromium', bucket='try')

  def _generate_variant(**kwargs):
    variant = resultdb_common.Variant()
    variant_def = getattr(variant, 'def')
    for k, v in kwargs.items():
      variant_def[str(k)] = str(v)
    return variant

  all_mismatched = _generate_variant(
      os='Ubuntu-14.04',
      test_suite='ios_chrome_bookmarks_eg2tests_module_iPhone 11 14.4')
  correct_variant = _generate_variant(
      os='Mac-11',
      test_suite='ios_chrome_bookmarks_eg2tests_module_iPad Air 2 14.4')
  correct_variant_another_suite = _generate_variant(
      os='Mac-11', test_suite='ios_chrome_web_eg2tests_module_iPad Air 2 14.4')

  # Populate build_database with two builds and two invocations.
  # Generating 10 builds into the build_database, each with an invocation
  # containing 2 failed test results. Total of 20 test results.
  test_id = (
      'ninja://ios/chrome/test/earl_grey2:ios_chrome_bookmarks_eg2tests_module/'
      'TestSuite.test_a')

  same_suite_another_test_id = (
      'ninja://ios/chrome/test/earl_grey2:ios_chrome_bookmarks_eg2tests_module/'
      'TestSuite.test_b')

  another_suite_another_test_id = (
      'ninja://ios/chrome/test/earl_grey2:ios_chrome_web_eg2tests_module/'
      'TestSuite.test_c')

  def _generate_build(builder, invocation, build_input=None):
    return build_pb2.Build(
        builder=builder,
        infra=build_pb2.BuildInfra(
            resultdb=build_pb2.BuildInfra.ResultDB(invocation=invocation)),
        input=build_input)

  def _generate_test_result(test_id,
                            test_variant,
                            status=None,
                            test_duration=10):
    status = status or test_result_pb2.PASS
    vd = getattr(test_variant, 'def')
    vh_in = '\n'.join('{}:{}'.format(k, v) for k, v in vd.items())
    vh = base64.b64encode(vh_in.encode('utf-8')).decode('utf-8')
    duration = duration_pb2.Duration()
    duration.FromMilliseconds(test_duration)
    tr = test_result_pb2.TestResult(
        test_id=test_id,
        variant_hash=vh,
        expected=status == test_result_pb2.PASS,
        status=status,
        duration=duration,
    )
    return tr

  def _generate_test_verdict(test_id,
                             test_variant,
                             invocation_id=None,
                             status=None):
    status = status or test_verdict.TestVerdictStatus.EXPECTED
    vd = getattr(test_variant, 'def')
    vh_in = '\n'.join('{}:{}'.format(k, v) for k, v in vd.items())
    vh = base64.b64encode(vh_in.encode('utf-8')).decode('utf-8')
    tv = test_verdict.TestVerdict(
        test_id=test_id,
        variant_hash=vh,
        invocation_id=invocation_id,
        status=status,
    )
    return tv

  build_database = []
  current_patchset_bookmark_suite_invocations = {}
  current_patchset_web_suite_invocations = {}
  current_patchset_web_suite_invocations_long_test = {}
  for i in range(3):
    inv = "invocations/{}".format(i + 1)
    build = _generate_build(builder, inv)
    build_database.append(build)

    if i == 0:
      test_results_bookmark_suite = [
          _generate_test_result(test_id, correct_variant),
          _generate_test_result(same_suite_another_test_id, correct_variant),
      ]
    else:
      test_results_bookmark_suite = [
          _generate_test_result(test_id, correct_variant),
      ]
    current_patchset_bookmark_suite_invocations[inv] = api.resultdb.Invocation(
        test_results=test_results_bookmark_suite)

  test_results_web_suite = [
      _generate_test_result(
          another_suite_another_test_id,
          correct_variant_another_suite,
          test_duration=0),
  ]
  current_patchset_web_suite_invocations['invocations/web'] = (
      api.resultdb.Invocation(test_results=test_results_web_suite))

  test_results_web_suite_long_test = [
      _generate_test_result(
          another_suite_another_test_id,
          correct_variant_another_suite,
          test_duration=96000),
  ]

  current_patchset_web_suite_invocations_long_test['invocations/web'] = (
      api.resultdb.Invocation(test_results=test_results_web_suite_long_test))

  # This is what's been most recently run as part of verification, and will be
  # removed as it's a false positive.
  test_a_other_invocation_history_res = test_history.QueryTestHistoryResponse(
      verdicts=[
          _generate_test_verdict(
              test_id, all_mismatched, invocation_id='some_other_invocation'),
      ],
      next_page_token='dummy_token')
  empty_history_res = test_history.QueryTestHistoryResponse(
      verdicts=[], next_page_token='dummy_token')

  builder_db = ctbc.BuilderDatabase.create({
      'fake-group': {
          'fake-builder':
              ctbc.BuilderSpec.create(
                  chromium_config='chromium',
                  gclient_config='chromium',
              ),
          'fake-android-builder':
              ctbc.BuilderSpec.create(
                  android_config='base_config',
                  chromium_config='android',
                  gclient_config='chromium',
                  gclient_apply_config=[
                      'android',
                  ],
                  chromium_config_kwargs={
                      'BUILD_CONFIG': 'Release',
                      'TARGET_BITS': 32,
                      'TARGET_PLATFORM': 'android',
                  }),
      },
  })

  yield api.test(
      'basic_ios',
      api.chromium_tests_builder_config.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
          builder_db=builder_db,
          try_db=ctbc.TryDatabase.create({
              'fake-try-group': {
                  'fake-try-builder':
                      ctbc.TrySpec.create_for_single_mirror(
                          builder_group='fake-group',
                          buildername='fake-builder',
                      ),
              },
          })),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'isolated_scripts': [
                      {
                          "test":
                              "ios_chrome_bookmarks_eg2tests_module",
                          "name": ("ios_chrome_bookmarks_eg2tests_module_iPad "
                                   "Air 2 14.4"),
                          "swarming": {
                              "dimensions": {
                                  "os": "Mac-11"
                              },
                              "shards": 2,
                          },
                          "test_id_prefix":
                              ("ninja://ios/chrome/test/earl_grey2:"
                               "ios_chrome_bookmarks_eg2tests_module/")
                      },
                      {
                          "test":
                              "ios_chrome_web_eg2tests_module",
                          "name": ("ios_chrome_web_eg2tests_module_iPad "
                                   "Air 2 14.4"),
                          "swarming": {
                              "dimensions": {
                                  "os": "Mac-11"
                              },
                              "shards": 1,
                          },
                          "test_id_prefix":
                              ("ninja://ios/chrome/test/earl_grey2:"
                               "ios_chrome_web_eg2tests_module/")
                      },
                  ],
              },
          }),
      api.flakiness(check_for_flakiness=True),
      api.override_step_data(
          ('ios_chrome_bookmarks_eg2tests_module_iPad Air 2 14.4 '
           '(with patch) on Mac-11'),
          api.chromium_swarming.canned_summary_output(
              api.test_utils.canned_isolated_script_output(), failure=False)),
      api.override_step_data(('ios_chrome_web_eg2tests_module_iPad Air 2 14.4 '
                              '(with patch) on Mac-11'),
                             api.chromium_swarming.canned_summary_output(
                                 api.test_utils.canned_isolated_script_output(),
                                 failure=False)),
      api.resultdb.query(
          current_patchset_bookmark_suite_invocations,
          ('collect tasks (with patch).'
           'ios_chrome_bookmarks_eg2tests_module_iPad Air 2 14.4 results'),
      ),
      api.resultdb.query(
          current_patchset_web_suite_invocations,
          ('collect tasks (with patch).'
           'ios_chrome_web_eg2tests_module_iPad Air 2 14.4 results'),
      ),
      # This overrides the file check to ensure that we have test files
      # in the given patch.
      api.step_data(
          'git diff to analyze patch (2)',
          api.raw_io.stream_output('chrome/test.cc\ncomponents/file2.cc')),
      api.luci_analysis.query_test_history(
          test_a_other_invocation_history_res,
          ('ninja://ios/chrome/test/earl_grey2:'
           'ios_chrome_bookmarks_eg2tests_module/TestSuite.test_a'),
          parent_step_name='searching_for_new_tests',
      ),
      api.luci_analysis.query_test_history(
          empty_history_res,
          ('ninja://ios/chrome/test/earl_grey2:'
           'ios_chrome_bookmarks_eg2tests_module/TestSuite.test_b'),
          parent_step_name='searching_for_new_tests',
      ),
      api.luci_analysis.query_test_history(
          empty_history_res,
          ('ninja://ios/chrome/test/earl_grey2:'
           'ios_chrome_web_eg2tests_module/TestSuite.test_c'),
          parent_step_name='searching_for_new_tests',
      ),
      api.override_step_data(
          ('test new tests for flakiness.'
           'ios_chrome_bookmarks_eg2tests_module_iPad Air 2 14.4 '
           '(check flakiness shard #0) on Mac-11'),
          api.chromium_swarming.canned_summary_output(
              api.test_utils.canned_isolated_script_output(), failure=False)),
      api.override_step_data(('test new tests for flakiness.'
                              'ios_chrome_web_eg2tests_module_iPad Air 2 14.4 '
                              '(check flakiness shard #0) on Mac-11'),
                             api.chromium_swarming.canned_summary_output(
                                 api.test_utils.canned_isolated_script_output(),
                                 failure=False)),
      api.resultdb.query(
          inv_bundle=current_patchset_bookmark_suite_invocations,
          step_name=(
              'test new tests for flakiness.'
              'collect tasks (check flakiness shard #0).'
              'ios_chrome_bookmarks_eg2tests_module_iPad Air 2 14.4 results')),
      api.resultdb.query(
          inv_bundle=current_patchset_web_suite_invocations,
          step_name=('test new tests for flakiness.'
                     'collect tasks (check flakiness shard #0).'
                     'ios_chrome_web_eg2tests_module_iPad Air 2 14.4 results')),
      api.post_process(
          post_process.LogContains,
          ('test new tests for flakiness.test_pre_run (check flakiness shard '
           '#0).[trigger] ios_chrome_bookmarks_eg2tests_module_iPad Air 2 14.4 '
           '(check flakiness shard #0) on Mac-11'),
          'json.input',
          ['\"priority\": \"29\"'],
      ),
      api.post_process(post_process.DropExpectation),
  )

  linux_variant = _generate_variant(
      os='Ubuntu-16', test_suite='check_network_annotations')
  script_invocation = {
      'invocations/build:8945511751514863184':
          api.resultdb.Invocation(test_results=[
              _generate_test_result(
                  test_id='check_network_annotations',
                  test_variant=linux_variant)
          ])
  }

  yield api.test(
      'basic_scripts_tests',
      api.chromium_tests_builder_config.try_build(
          builder_group='fake-try-group',
          builder='fake-android-try-builder',
          builder_db=builder_db,
          try_db=ctbc.TryDatabase.create({
              'fake-try-group': {
                  'fake-android-try-builder':
                      ctbc.TrySpec.create_for_single_mirror(
                          builder_group='fake-group',
                          buildername='fake-android-builder',
                      ),
              },
          })),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-android-builder': {
                  'scripts': [{
                      'isolate_profile_data': True,
                      'name': 'check_network_annotations',
                      'resultdb': {
                          'enable': True,
                          'has_native_resultdb_integration': True
                      },
                      'script': 'check_network_annotations.py',
                  }],
              },
          }),
      api.flakiness(check_for_flakiness=True),
      api.resultdb.query(
          script_invocation,
          ('check_network_annotations results'),
      ),
      api.step_data(
          'git diff to analyze patch (2)',
          api.raw_io.stream_output('chrome/test.cc\ncomponents/file2.cc')),
      api.luci_analysis.query_test_history(
          empty_history_res,
          'check_network_annotations',
          parent_step_name='searching_for_new_tests',
      ),
      api.resultdb.query(
          inv_bundle=script_invocation,
          step_name=('test new tests for flakiness.'
                     'check_network_annotations results')),
      api.post_process(post_process.DropExpectation),
  )

  test_id_base_unittests_test_d = 'ninja://base:base_unittests/TestSuite.test_d'
  base_unittests_pass_invocations = {}
  base_unittests_pass_invocations['invocations/pass'] = (
      api.resultdb.Invocation(test_results=[
          _generate_test_result(test_id_base_unittests_test_d, correct_variant),
      ]))

  yield api.test(
      'basic_local_gtest',
      api.chromium_tests_builder_config.try_build(
          builder_group='fake-try-group',
          builder='fake-android-try-builder',
          builder_db=builder_db,
          try_db=ctbc.TryDatabase.create({
              'fake-try-group': {
                  'fake-android-try-builder':
                      ctbc.TrySpec.create_for_single_mirror(
                          builder_group='fake-group',
                          buildername='fake-android-builder',
                      ),
              },
          })),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-android-builder': {
                  'isolated_scripts': [{
                      "test": "base_unittests",
                      "name": "base_unittests",
                      "test_id_prefix": "ninja://base:base_unittests/",
                  },],
              },
          }),
      api.flakiness(check_for_flakiness=True),
      api.resultdb.query(
          base_unittests_pass_invocations,
          'base_unittests results',
      ),
      # This overrides the file check to ensure that we have test files
      # in the given patch.
      api.step_data(
          'git diff to analyze patch (2)',
          api.raw_io.stream_output('chrome/test.cc\ncomponents/file2.cc')),
      api.luci_analysis.query_test_history(
          empty_history_res,
          'ninja://base:base_unittests/TestSuite.test_d',
          parent_step_name='searching_for_new_tests',
      ),
      api.resultdb.query(
          inv_bundle=script_invocation,
          step_name=('test new tests for flakiness.'
                     'base_unittests results')),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'basic_ios_sharded',
      api.chromium_tests_builder_config.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
          builder_db=builder_db,
          try_db=ctbc.TryDatabase.create({
              'fake-try-group': {
                  'fake-try-builder':
                      ctbc.TrySpec.create_for_single_mirror(
                          builder_group='fake-group',
                          buildername='fake-builder',
                      ),
              },
          })),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'isolated_scripts': [{
                      "test":
                          "ios_chrome_web_eg2tests_module",
                      "name": ("ios_chrome_web_eg2tests_module_iPad "
                               "Air 2 14.4"),
                      "swarming": {
                          "dimensions": {
                              "os": "Mac-11"
                          },
                          "shards": 1,
                      },
                      "test_id_prefix": ("ninja://ios/chrome/test/earl_grey2:"
                                         "ios_chrome_web_eg2tests_module/")
                  },],
              },
          }),
      api.flakiness(check_for_flakiness=True),
      api.override_step_data(('ios_chrome_web_eg2tests_module_iPad Air 2 14.4 '
                              '(with patch) on Mac-11'),
                             api.chromium_swarming.canned_summary_output(
                                 api.test_utils.canned_isolated_script_output(),
                                 failure=False)),
      api.resultdb.query(
          current_patchset_web_suite_invocations_long_test,
          ('collect tasks (with patch).'
           'ios_chrome_web_eg2tests_module_iPad Air 2 14.4 results'),
      ),
      # This overrides the file check to ensure that we have test files
      # in the given patch.
      api.step_data(
          'git diff to analyze patch (2)',
          api.raw_io.stream_output('chrome/test.cc\ncomponents/file2.cc')),
      # This is what's been recently run, and that isn't in the exclusion list
      # and so should be removed (false positive).
      api.luci_analysis.query_test_history(
          empty_history_res,
          ('ninja://ios/chrome/test/earl_grey2:'
           'ios_chrome_web_eg2tests_module/TestSuite.test_c'),
          parent_step_name='searching_for_new_tests',
      ),
      api.override_step_data(('test new tests for flakiness.'
                              'ios_chrome_web_eg2tests_module_iPad Air 2 14.4 '
                              '(check flakiness shard #0) on Mac-11'),
                             api.chromium_swarming.canned_summary_output(
                                 api.test_utils.canned_isolated_script_output(),
                                 failure=False)),
      api.override_step_data(('test new tests for flakiness.'
                              'ios_chrome_web_eg2tests_module_iPad Air 2 14.4 '
                              '(check flakiness shard #1) on Mac-11'),
                             api.chromium_swarming.canned_summary_output(
                                 api.test_utils.canned_isolated_script_output(),
                                 failure=False)),
      api.resultdb.query(
          inv_bundle=current_patchset_web_suite_invocations,
          step_name=('test new tests for flakiness.'
                     'collect tasks (check flakiness shard #0).'
                     'ios_chrome_web_eg2tests_module_iPad Air 2 14.4 results')),
      api.resultdb.query(
          inv_bundle=current_patchset_web_suite_invocations,
          step_name=('test new tests for flakiness.'
                     'collect tasks (check flakiness shard #1).'
                     'ios_chrome_web_eg2tests_module_iPad Air 2 14.4 results')),
      api.post_process(post_process.DropExpectation),
  )

  flaky_results = {
      'invocations/100':
          api.resultdb.Invocation(test_results=[
              _generate_test_result(test_id, correct_variant),
              _generate_test_result(
                  test_id, correct_variant, status=test_result_pb2.FAIL)
          ])
  }

  test_id_base_unittests_test_d = 'ninja://base:base_unittests/TestSuite.test_d'
  base_unittests_pass_invocations = {}
  base_unittests_pass_invocations['invocations/pass'] = (
      api.resultdb.Invocation(test_results=[
          _generate_test_result(test_id_base_unittests_test_d, correct_variant),
      ]))
  base_unittests_flaky_invocations = {}
  base_unittests_flaky_invocations['invocations/flaky'] = (
      api.resultdb.Invocation(test_results=[
          _generate_test_result(
              test_id_base_unittests_test_d,
              correct_variant,
              status=test_result_pb2.FAIL),
          _generate_test_result(test_id_base_unittests_test_d, correct_variant),
      ]))

  yield api.test(
      # GTest has non zero exit code at swarming task at "check flakiness" runs
      # if there are flakiness within. This will be determined as "invalid"
      # test step by recipe logic. This test ensures we still present flakiness
      # statistics for these.
      'basic_gtest_failure',
      api.chromium_tests_builder_config.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
          builder_db=builder_db,
          try_db=ctbc.TryDatabase.create({
              'fake-try-group': {
                  'fake-try-builder':
                      ctbc.TrySpec.create_for_single_mirror(
                          builder_group='fake-group',
                          buildername='fake-builder',
                      ),
              },
          })),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'isolated_scripts': [{
                      "test": "base_unittests",
                      "name": "base_unittests",
                      "swarming": {
                          "dimensions": {
                              "os": "Mac-11"
                          },
                      },
                      "test_id_prefix": "ninja://base:base_unittests/",
                  },],
              },
          }),
      api.flakiness(check_for_flakiness=True),
      api.override_step_data(('base_unittests '
                              '(with patch) on Mac-11'),
                             api.chromium_swarming.canned_summary_output(
                                 api.test_utils.canned_isolated_script_output(),
                                 failure=False)),
      api.resultdb.query(
          base_unittests_pass_invocations,
          ('collect tasks (with patch).'
           'base_unittests results'),
      ),
      # This overrides the file check to ensure that we have test files
      # in the given patch.
      api.step_data(
          'git diff to analyze patch (2)',
          api.raw_io.stream_output('chrome/test.cc\ncomponents/file2.cc')),
      api.luci_analysis.query_test_history(
          empty_history_res,
          ('ninja://base:base_unittests/TestSuite.test_d'),
          parent_step_name='searching_for_new_tests',
      ),
      api.override_step_data(('test new tests for flakiness.'
                              'base_unittests '
                              '(check flakiness shard #0) on Mac-11'),
                             api.chromium_swarming.canned_summary_output(
                                 api.test_utils.canned_isolated_script_output(),
                                 failure=True)),
      api.resultdb.query(
          inv_bundle=base_unittests_flaky_invocations,
          step_name=('test new tests for flakiness.'
                     'collect tasks (check flakiness shard #0).'
                     'base_unittests results')),
      api.post_check(post_process.MustRun, 'calculate flake rates'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  def CheckRecordedFlakyTest(check, step_odict, test):
    flaky_suites = post_process.GetBuildProperties(step_odict).get(
        'flake_endorser_rejections', {}).get('flaky_suites', '')
    check(test in flaky_suites)

  yield api.test(
      'basic_ios_test_flaky',
      api.chromium_tests_builder_config.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
          builder_db=builder_db,
          try_db=ctbc.TryDatabase.create({
              'fake-try-group': {
                  'fake-try-builder':
                      ctbc.TrySpec.create_for_single_mirror(
                          builder_group='fake-group',
                          buildername='fake-builder',
                      ),
              },
          })),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'isolated_scripts': [{
                      "test":
                          "ios_chrome_bookmarks_eg2tests_module",
                      "name": ("ios_chrome_bookmarks_eg2tests_module_iPad "
                               "Air 2 14.4"),
                      "swarming": {
                          "dimensions": {
                              "os": "Mac-11"
                          },
                      },
                      "test_id_prefix":
                          ("ninja://ios/chrome/test/earl_grey2:"
                           "ios_chrome_bookmarks_eg2tests_module/")
                  },],
              },
          }),
      api.flakiness(check_for_flakiness=True),
      api.override_step_data(
          ('ios_chrome_bookmarks_eg2tests_module_iPad Air 2 14.4 '
           '(with patch) on Mac-11'),
          api.chromium_swarming.canned_summary_output(
              api.test_utils.canned_isolated_script_output(), failure=False)),
      api.resultdb.query(
          current_patchset_bookmark_suite_invocations,
          ('collect tasks (with patch).'
           'ios_chrome_bookmarks_eg2tests_module_iPad Air 2 14.4 results'),
      ),
      # This overrides the file check to ensure that we have test files
      # in the given patch.
      api.step_data(
          'git diff to analyze patch (2)',
          api.raw_io.stream_output('chrome/test.cc\ncomponents/file2.cc')),
      api.luci_analysis.query_test_history(
          test_a_other_invocation_history_res,
          ('ninja://ios/chrome/test/earl_grey2:'
           'ios_chrome_bookmarks_eg2tests_module/TestSuite.test_a'),
          parent_step_name='searching_for_new_tests',
      ),
      api.luci_analysis.query_test_history(
          empty_history_res,
          ('ninja://ios/chrome/test/earl_grey2:'
           'ios_chrome_bookmarks_eg2tests_module/TestSuite.test_b'),
          parent_step_name='searching_for_new_tests',
      ),
      api.override_step_data(
          ('test new tests for flakiness.'
           'ios_chrome_bookmarks_eg2tests_module_iPad Air 2 14.4 '
           '(check flakiness shard #0) on Mac-11'),
          api.chromium_swarming.canned_summary_output(
              api.test_utils.canned_isolated_script_output(), failure=True)),
      api.resultdb.query(
          inv_bundle=flaky_results,
          step_name=(
              'test new tests for flakiness.'
              'collect tasks (check flakiness shard #0).'
              'ios_chrome_bookmarks_eg2tests_module_iPad Air 2 14.4 results')),
      api.post_process(CheckRecordedFlakyTest,
                       'ios_chrome_bookmarks_eg2tests_module_iPad Air 2 14.4'),
      api.post_check(post_process.MustRun, 'calculate flake rates'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  def CheckRecordedInvalidTest(check, step_odict, test):
    flaky_suites = post_process.GetBuildProperties(step_odict).get(
        'flake_endorser_rejections', {}).get('invalid_suites', '')
    check(test in flaky_suites)

  yield api.test(
      'basic_ios_test_invalid',
      api.chromium_tests_builder_config.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
          builder_db=builder_db,
          try_db=ctbc.TryDatabase.create({
              'fake-try-group': {
                  'fake-try-builder':
                      ctbc.TrySpec.create_for_single_mirror(
                          builder_group='fake-group',
                          buildername='fake-builder',
                      ),
              },
          })),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'isolated_scripts': [{
                      "test":
                          "ios_chrome_bookmarks_eg2tests_module",
                      "name": ("ios_chrome_bookmarks_eg2tests_module_iPad "
                               "Air 2 14.4"),
                      "swarming": {
                          "dimensions": {
                              "os": "Mac-11"
                          },
                      },
                      "test_id_prefix":
                          ("ninja://ios/chrome/test/earl_grey2:"
                           "ios_chrome_bookmarks_eg2tests_module/")
                  },],
              },
          }),
      api.flakiness(check_for_flakiness=True),
      api.override_step_data(
          ('ios_chrome_bookmarks_eg2tests_module_iPad Air 2 14.4 '
           '(with patch) on Mac-11'),
          api.chromium_swarming.canned_summary_output(
              api.test_utils.canned_isolated_script_output(), failure=False)),
      api.resultdb.query(
          current_patchset_bookmark_suite_invocations,
          ('collect tasks (with patch).'
           'ios_chrome_bookmarks_eg2tests_module_iPad Air 2 14.4 results'),
      ),
      # This overrides the file check to ensure that we have test files
      # in the given patch.
      api.step_data(
          'git diff to analyze patch (2)',
          api.raw_io.stream_output('chrome/test.cc\ncomponents/file2.cc')),
      api.luci_analysis.query_test_history(
          test_a_other_invocation_history_res,
          ('ninja://ios/chrome/test/earl_grey2:'
           'ios_chrome_bookmarks_eg2tests_module/TestSuite.test_a'),
          parent_step_name='searching_for_new_tests',
      ),
      api.luci_analysis.query_test_history(
          empty_history_res,
          ('ninja://ios/chrome/test/earl_grey2:'
           'ios_chrome_bookmarks_eg2tests_module/TestSuite.test_b'),
          parent_step_name='searching_for_new_tests',
      ),
      api.override_step_data(
          ('test new tests for flakiness.'
           'ios_chrome_bookmarks_eg2tests_module_iPad Air 2 14.4 '
           '(check flakiness shard #0) on Mac-11'),
          api.chromium_swarming.canned_summary_output(
              api.test_utils.canned_isolated_script_output(), failure=True)),
      # Result from "check flakiness" step is empty.
      api.post_check(
          post_process.SummaryMarkdown,
          'ios_chrome_bookmarks_eg2tests_module_iPad Air 2 14.4 '
          '(check flakiness shard #0) steps in '
          "test new tests for flakiness didn't produce test results."),
      api.post_process(CheckRecordedInvalidTest,
                       'ios_chrome_bookmarks_eg2tests_module_iPad Air 2 14.4'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'basic_ios_test_failure_experimental_suite',
      api.chromium_tests_builder_config.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
          builder_db=builder_db,
          try_db=ctbc.TryDatabase.create({
              'fake-try-group': {
                  'fake-try-builder':
                      ctbc.TrySpec.create_for_single_mirror(
                          builder_group='fake-group',
                          buildername='fake-builder',
                      ),
              },
          })),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'isolated_scripts': [{
                      "test":
                          "ios_chrome_bookmarks_eg2tests_module",
                      "name": ("ios_chrome_bookmarks_eg2tests_module_iPad "
                               "Air 2 14.4"),
                      "swarming": {
                          "dimensions": {
                              "os": "Mac-11"
                          },
                      },
                      "experiment_percentage":
                          100,
                      "test_id_prefix":
                          ("ninja://ios/chrome/test/earl_grey2:"
                           "ios_chrome_bookmarks_eg2tests_module/")
                  },],
              },
          }),
      api.flakiness(check_for_flakiness=True),
      api.override_step_data(
          ('ios_chrome_bookmarks_eg2tests_module_iPad Air 2 14.4 '
           '(with patch, experimental) on Mac-11'),
          api.chromium_swarming.canned_summary_output(
              api.test_utils.canned_isolated_script_output(), failure=False)),
      api.resultdb.query(
          current_patchset_bookmark_suite_invocations,
          ('collect tasks (with patch).'
           'ios_chrome_bookmarks_eg2tests_module_iPad Air 2 14.4 results'),
      ),
      # This overrides the file check to ensure that we have test files
      # in the given patch.
      api.step_data(
          'git diff to analyze patch (2)',
          api.raw_io.stream_output('chrome/test.cc\ncomponents/file2.cc')),
      api.luci_analysis.query_test_history(
          test_a_other_invocation_history_res,
          ('ninja://ios/chrome/test/earl_grey2:'
           'ios_chrome_bookmarks_eg2tests_module/TestSuite.test_a'),
          parent_step_name='searching_for_new_tests',
      ),
      api.luci_analysis.query_test_history(
          empty_history_res,
          ('ninja://ios/chrome/test/earl_grey2:'
           'ios_chrome_bookmarks_eg2tests_module/TestSuite.test_b'),
          parent_step_name='searching_for_new_tests',
      ),
      api.override_step_data(
          ('test new tests for flakiness.'
           'ios_chrome_bookmarks_eg2tests_module_iPad Air 2 14.4 '
           '(check flakiness shard #0, experimental) on Mac-11'),
          api.chromium_swarming.canned_summary_output(
              api.test_utils.canned_isolated_script_output(), failure=True)),
      api.resultdb.query(
          inv_bundle=flaky_results,
          step_name=(
              'test new tests for flakiness.'
              'collect tasks (check flakiness shard #0).'
              'ios_chrome_bookmarks_eg2tests_module_iPad Air 2 14.4 results')),
      api.post_process(CheckRecordedFlakyTest,
                       'ios_chrome_bookmarks_eg2tests_module_iPad Air 2 14.4'),
      api.post_process(post_process.DropExpectation),
  )

  test_id_bookmarks_suite_test_d = (
      'ninja://ios/chrome/test/earl_grey2:ios_chrome_bookmarks_eg2tests_module/'
      'TestSuite.test_d')
  bookmark_suite_flaky_invocations = {}
  bookmark_suite_flaky_invocations['invocations/flaky'] = (
      api.resultdb.Invocation(test_results=[
          _generate_test_result(
              test_id_bookmarks_suite_test_d,
              correct_variant,
              status=test_result_pb2.FAIL),
          _generate_test_result(test_id_bookmarks_suite_test_d,
                                correct_variant),
      ]))

  yield api.test(
      'early_fail_for_flakiness_in_with_patch_runs_ios',
      api.chromium_tests_builder_config.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
          builder_db=builder_db,
          try_db=ctbc.TryDatabase.create({
              'fake-try-group': {
                  'fake-try-builder':
                      ctbc.TrySpec.create_for_single_mirror(
                          builder_group='fake-group',
                          buildername='fake-builder',
                      ),
              },
          })),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'isolated_scripts': [{
                      "test":
                          "ios_chrome_bookmarks_eg2tests_module",
                      "name": ("ios_chrome_bookmarks_eg2tests_module_iPad "
                               "Air 2 14.4"),
                      "swarming": {
                          "dimensions": {
                              "os": "Mac-11"
                          },
                          "shards": 2,
                      },
                      "test_id_prefix":
                          ("ninja://ios/chrome/test/earl_grey2:"
                           "ios_chrome_bookmarks_eg2tests_module/")
                  },],
              },
          }),
      api.flakiness(check_for_flakiness=True),
      api.override_step_data(
          ('ios_chrome_bookmarks_eg2tests_module_iPad Air 2 14.4 '
           '(with patch) on Mac-11'),
          api.chromium_swarming.canned_summary_output(
              api.test_utils.canned_isolated_script_output(), failure=False)),
      api.resultdb.query(
          bookmark_suite_flaky_invocations,
          ('collect tasks (with patch).'
           'ios_chrome_bookmarks_eg2tests_module_iPad Air 2 14.4 results'),
      ),
      # This overrides the file check to ensure that we have test files
      # in the given patch.
      api.step_data(
          'git diff to analyze patch (2)',
          api.raw_io.stream_output('chrome/test.cc\ncomponents/file2.cc')),
      api.luci_analysis.query_test_history(
          empty_history_res,
          ('ninja://ios/chrome/test/earl_grey2:'
           'ios_chrome_bookmarks_eg2tests_module/TestSuite.test_d'),
          parent_step_name='searching_for_new_tests',
      ),
      api.post_check(post_process.StepTextContains, 'calculate flake rates', [
          'Test: **TestSuite.test_d**', '# of failures: 1, total # of runs: 2.',
          '- ios_chrome_bookmarks_eg2tests_module_iPad Air 2 14.4 (with patch)'
      ]),
      api.post_check(post_process.DoesNotRun, 'test new tests for flakiness'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  bookmark_suite_failed_invocations = {}
  bookmark_suite_failed_invocations['invocations/flaky'] = (
      api.resultdb.Invocation(test_results=[
          _generate_test_result(
              test_id_bookmarks_suite_test_d,
              correct_variant,
              status=test_result_pb2.FAIL),
          _generate_test_result(
              test_id_bookmarks_suite_test_d,
              correct_variant,
              status=test_result_pb2.FAIL),
      ]))

  yield api.test(
      'early_fail_for_flakiness_in_with_patch_retry_shards_runs_ios',
      api.chromium_tests_builder_config.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
          builder_db=builder_db,
          try_db=ctbc.TryDatabase.create({
              'fake-try-group': {
                  'fake-try-builder':
                      ctbc.TrySpec.create_for_single_mirror(
                          builder_group='fake-group',
                          buildername='fake-builder',
                      ),
              },
          })),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'isolated_scripts': [{
                      "test":
                          "ios_chrome_bookmarks_eg2tests_module",
                      "name": ("ios_chrome_bookmarks_eg2tests_module_iPad "
                               "Air 2 14.4"),
                      "swarming": {
                          "dimensions": {
                              "os": "Mac-11"
                          },
                          "shards": 2,
                      },
                      "test_id_prefix":
                          ("ninja://ios/chrome/test/earl_grey2:"
                           "ios_chrome_bookmarks_eg2tests_module/")
                  },],
              },
          }),
      api.flakiness(check_for_flakiness=True),
      api.override_step_data(
          ('ios_chrome_bookmarks_eg2tests_module_iPad Air 2 14.4 '
           '(with patch) on Mac-11'),
          api.chromium_swarming.canned_summary_output(
              api.test_utils.canned_isolated_script_output(), failure=True)),
      api.resultdb.query(
          bookmark_suite_failed_invocations,
          ('collect tasks (with patch).'
           'ios_chrome_bookmarks_eg2tests_module_iPad Air 2 14.4 results'),
      ),
      api.resultdb.query(
          bookmark_suite_flaky_invocations,
          ('collect tasks (retry shards with patch).'
           'ios_chrome_bookmarks_eg2tests_module_iPad Air 2 14.4 results'),
      ),
      # This overrides the file check to ensure that we have test files
      # in the given patch.
      api.step_data(
          'git diff to analyze patch (2)',
          api.raw_io.stream_output('chrome/test.cc\ncomponents/file2.cc')),
      api.luci_analysis.query_test_history(
          empty_history_res,
          ('ninja://ios/chrome/test/earl_grey2:'
           'ios_chrome_bookmarks_eg2tests_module/TestSuite.test_d'),
          parent_step_name='searching_for_new_tests',
      ),
      api.post_check(post_process.StepTextContains, 'calculate flake rates', [
          'Test: **TestSuite.test_d**',
          '# of failures: 3, total # of runs: 4.',
          '- ios_chrome_bookmarks_eg2tests_module_iPad Air 2 14.4 (with patch)',
          '- ios_chrome_bookmarks_eg2tests_module_iPad Air 2 14.4 '
          '(retry shards with patch)',
      ]),
      api.post_check(post_process.DoesNotRun, 'test new tests for flakiness'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'skip identifying at DEPS file change',
      api.chromium_tests_builder_config.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
          builder_db=builder_db,
          try_db=ctbc.TryDatabase.create({
              'fake-try-group': {
                  'fake-try-builder':
                      ctbc.TrySpec.create_for_single_mirror(
                          builder_group='fake-group',
                          buildername='fake-builder',
                      ),
              },
          })),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'isolated_scripts': [{
                      "test": "base_unittests",
                      "name": "base_unittests",
                      "swarming": {
                          "dimensions": {
                              "os": "Mac-11"
                          },
                      },
                      "test_id_prefix": "ninja://base:base_unittests/",
                  },],
              },
          }),
      api.flakiness(check_for_flakiness=True),
      api.step_data('git diff to analyze patch (2)',
                    api.raw_io.stream_output('chrome/file1.cc\nsrc/DEPS')),
      api.post_check(post_process.MustRun,
                     'no test files were detected with this change.'),
      api.post_check(post_process.DoesNotRun, 'searching_for_new_tests'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'skip identifying when testing/buldbot',
      api.chromium_tests_builder_config.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
          builder_db=builder_db,
          try_db=ctbc.TryDatabase.create({
              'fake-try-group': {
                  'fake-try-builder':
                      ctbc.TrySpec.create_for_single_mirror(
                          builder_group='fake-group',
                          buildername='fake-builder',
                      ),
              },
          })),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'isolated_scripts': [{
                      "test": "base_unittests",
                      "name": "base_unittests",
                      "swarming": {
                          "dimensions": {
                              "os": "Mac-11"
                          },
                      },
                      "test_id_prefix": "ninja://base:base_unittests/",
                  },],
              },
          }),
      api.flakiness(check_for_flakiness=True),
      api.step_data(
          'git diff to analyze patch (2)',
          api.raw_io.stream_output('testing/buildbot/test_suites.pyl\n'
                                   'testing/buildbot/waterfalls.pyl')),
      api.post_check(post_process.MustRun,
                     'no test files were detected with this change.'),
      api.post_check(post_process.DoesNotRun, 'searching_for_new_tests'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'skip identifying when no test file change',
      api.chromium_tests_builder_config.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
          builder_db=builder_db,
          try_db=ctbc.TryDatabase.create({
              'fake-try-group': {
                  'fake-try-builder':
                      ctbc.TrySpec.create_for_single_mirror(
                          builder_group='fake-group',
                          buildername='fake-builder',
                      ),
              },
          })),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'isolated_scripts': [{
                      "test": "base_unittests",
                      "name": "base_unittests",
                      "swarming": {
                          "dimensions": {
                              "os": "Mac-11"
                          },
                      },
                      "test_id_prefix": "ninja://base:base_unittests/",
                  },],
              },
          }),
      api.flakiness(check_for_flakiness=True),
      api.step_data(
          'git diff to analyze patch (2)',
          api.raw_io.stream_output('chrome/file1.cc\ncomponents/file2.cc')),
      api.post_check(post_process.MustRun,
                     'no test files were detected with this change.'),
      api.post_check(post_process.DoesNotRun, 'searching_for_new_tests'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'skip footer',
      api.chromium_tests_builder_config.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
          builder_db=builder_db,
          try_db=ctbc.TryDatabase.create({
              'fake-try-group': {
                  'fake-try-builder':
                      ctbc.TrySpec.create_for_single_mirror(
                          builder_group='fake-group',
                          buildername='fake-builder',
                      ),
              },
          })),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'isolated_scripts': [{
                      "test": "base_unittests",
                      "name": "base_unittests",
                      "swarming": {
                          "dimensions": {
                              "os": "Mac-11"
                          },
                      },
                      "test_id_prefix": "ninja://base:base_unittests/",
                  },],
              },
          }),
      api.flakiness(check_for_flakiness=True),
      api.step_data('parse description',
                    api.json.output({'Validate-Test-Flakiness': ['Skip']})),
      api.post_check(
          post_process.MustRun, 'skipping flaky test check since commit footer '
          '\'Validate-Test-Flakiness: Skip\' was detected.'),
      api.post_check(post_process.DoesNotRun, 'searching_for_new_tests'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'no identification',
      api.chromium_tests_builder_config.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
          builder_db=builder_db,
          try_db=ctbc.TryDatabase.create({
              'fake-try-group': {
                  'fake-try-builder':
                      ctbc.TrySpec.create_for_single_mirror(
                          builder_group='fake-group',
                          buildername='fake-builder',
                      ),
              },
          })),
      api.flakiness(check_for_flakiness=False,),
      api.post_process(post_process.DropExpectation),
  )
