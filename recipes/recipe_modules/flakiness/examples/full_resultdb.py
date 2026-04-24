# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import base64

from google.protobuf import duration_pb2

from recipe_engine import post_process

from PB.go.chromium.org.luci.resultdb.proto.v1 \
    import common as rdb_common_pb2
from PB.go.chromium.org.luci.resultdb.proto.v1 import resultdb as rdb_pb2
from PB.go.chromium.org.luci.resultdb.proto.v1 \
    import test_result as test_result_pb2

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

DEPS = [
    'chromium',
    'chromium_swarming',
    'chromium_tests',
    'chromium_tests_builder_config',
    'flakiness',
    'recipe_engine/assertions',
    'recipe_engine/step',
    'recipe_engine/raw_io',
    'recipe_engine/resultdb',
]


def RunSteps(api):
  # These steps are to mimic the Chromium try recipe before we actually call the
  # flakiness workflow for testing.
  b_id, b_config = api.chromium_tests_builder_config.lookup_builder()
  with api.chromium.chromium_layout():
    api.chromium_tests.trybot_steps(b_id, b_config)


def GenTests(api):
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

  ios_test_spec = {
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
              "test_id_prefix": ("ninja://ios/chrome/test/earl_grey2:"
                                 "ios_chrome_bookmarks_eg2tests_module/")
          },],
      },
  }

  inv = "invocations/build:8945511751514863184"

  def _generate_variant(**kwargs):
    variant = rdb_common_pb2.Variant()
    variant_def = getattr(variant, 'def')
    for k, v in kwargs.items():
      variant_def[str(k)] = str(v)
    return variant

  def _generate_variant_hash(test_variant):
    vd = getattr(test_variant, 'def')
    vh_in = '\n'.join('{}:{}'.format(k, v) for k, v in vd.items())
    return base64.b64encode(vh_in.encode('utf-8')).decode('utf-8')

  def _generate_test_result(test_id,
                            variant_hash,
                            status=None,
                            test_duration=10):
    status = status or test_result_pb2.PASS
    duration = duration_pb2.Duration()
    duration.FromMilliseconds(test_duration)
    tr = test_result_pb2.TestResult(
        test_id=test_id,
        variant_hash=variant_hash,
        expected=status == test_result_pb2.PASS,
        status=status,
        duration=duration,
    )
    return tr

  ################################# EDGE CASES #################################

  yield api.test(
      'no property, terminate early',
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
      api.chromium_tests.read_targets_spec('fake-group', ios_test_spec),
      api.flakiness(
          check_for_flakiness=False, check_for_flakiness_with_resultdb=False),
      api.post_check(post_process.DoesNotRun, 'searching_for_new_tests'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'query new test variants, baseline not ready',
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
      api.chromium_tests.read_targets_spec('fake-group', ios_test_spec),
      api.flakiness(check_for_flakiness_with_resultdb=True),
      # TODO (crbug/1456545) - This overrides the file check to ensure that we
      # have test files in the given patch. Remove when logic is removed.
      api.step_data(
          'git diff to analyze patch (2)',
          api.raw_io.stream_output('chrome/test.cc\ncomponents/file2.cc')),
      api.resultdb.query_new_test_variants(
          rdb_pb2.QueryNewTestVariantsResponse(
              is_baseline_ready=False, new_test_variants=[]),
          step_name=('searching_for_new_tests with ResultDB.'
                     'query_new_test_variants')),
      api.post_process(
          post_process.StepCommandContains,
          ('searching_for_new_tests with ResultDB.query_new_test_variants'), [
              'rdb',
              'rpc',
              'luci.resultdb.v1.ResultDB',
              'QueryNewTestVariants',
          ]),
      api.post_process(
          post_process.MustRun,
          'searching_for_new_tests with ResultDB.'
          'Baseline is not yet ready to calculate new tests',
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'query new test variants, baseline ready, no new tests',
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
      api.chromium_tests.read_targets_spec('fake-group', ios_test_spec),
      api.flakiness(check_for_flakiness_with_resultdb=True),
      # TODO (crbug/1456545) - This overrides the file check to ensure that we
      # have test files in the given patch. Remove when logic is removed.
      api.step_data(
          'git diff to analyze patch (2)',
          api.raw_io.stream_output('chrome/test.cc\ncomponents/file2.cc')),
      api.resultdb.query_new_test_variants(
          rdb_pb2.QueryNewTestVariantsResponse(
              is_baseline_ready=True, new_test_variants=[]),
          step_name=('searching_for_new_tests with ResultDB.'
                     'query_new_test_variants')),
      api.post_process(
          post_process.StepCommandContains,
          ('searching_for_new_tests with ResultDB.query_new_test_variants'), [
              'rdb',
              'rpc',
              'luci.resultdb.v1.ResultDB',
              'QueryNewTestVariants',
          ]),
      api.post_process(
          post_process.MustRun,
          'searching_for_new_tests with ResultDB.'
          'No new tests detected',
      ),
      api.post_process(post_process.DropExpectation),
  )

  ################################## IOS TEST ##################################

  ios_v = _generate_variant(
      os='Mac-11',
      test_suite='ios_chrome_bookmarks_eg2tests_module_iPad Air 2 14.4',
  )
  ios_vh = _generate_variant_hash(ios_v)

  ios_test1 = ('ninja://ios/chrome/test/earl_grey2:'
               'ios_chrome_bookmarks_eg2tests_module/TestSuite.test_a')
  ios_test2 = ('ninja://ios/chrome/test/earl_grey2:'
               'ios_chrome_bookmarks_eg2tests_module/TestSuite.test_b')
  ios_test3 = ('ninja://ios/chrome/test/earl_grey2:'
               'ios_chrome_bookmarks_eg2tests_module/TestSuite.test_c')

  current_build_ios_test_resuts = {
      inv:
          api.resultdb.Invocation(test_results=[
              _generate_test_result(ios_test1, ios_vh),
              _generate_test_result(ios_test2, ios_vh),
              _generate_test_result(ios_test3, ios_vh),
          ]),
  }

  yield api.test(
      'e2e: new iOS tests',
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
      api.chromium_tests.read_targets_spec('fake-group', ios_test_spec),
      api.flakiness(check_for_flakiness_with_resultdb=True),
      # TODO (crbug/1456545) - This overrides the file check to ensure that we
      # have test files in the given patch. Remove when logic is removed.
      api.step_data(
          'git diff to analyze patch (2)',
          api.raw_io.stream_output('chrome/test.cc\ncomponents/file2.cc')),
      api.resultdb.query(
          current_build_ios_test_resuts,
          ('collect tasks (with patch).'
           'ios_chrome_bookmarks_eg2tests_module_iPad Air 2 14.4 results'),
      ),
      api.resultdb.query_new_test_variants(
          rdb_pb2.QueryNewTestVariantsResponse(
              is_baseline_ready=True,
              new_test_variants=[
                  rdb_pb2.QueryNewTestVariantsResponse.NewTestVariant(
                      test_id=ios_test2,
                      variant_hash=ios_vh,
                  ),
                  rdb_pb2.QueryNewTestVariantsResponse.NewTestVariant(
                      test_id=ios_test3,
                      variant_hash=ios_vh,
                  ),
              ]),
          step_name=('searching_for_new_tests with ResultDB.'
                     'query_new_test_variants')),
      api.post_process(
          post_process.StepCommandContains,
          ('searching_for_new_tests with ResultDB.query_new_test_variants'), [
              'rdb',
              'rpc',
              'luci.resultdb.v1.ResultDB',
              'QueryNewTestVariants',
          ]),
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

  ############################## SHARDING TEST #################################

  # Reuse iOS information for this test, but set test duration to 96000 so that
  # we shard it.
  current_build_sharding_test_resuts = {
      inv:
          api.resultdb.Invocation(test_results=[
              _generate_test_result(ios_test1, ios_vh, test_duration=96000),
          ]),
  }

  single_shard_test_spec = ios_test_spec
  single_shard_test_spec['fake-builder']['isolated_scripts'][0]['swarming'][
      'shards'] = 2

  yield api.test(
      'e2e: sharding iOS tests',
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
      api.chromium_tests.read_targets_spec('fake-group',
                                           single_shard_test_spec),
      api.flakiness(check_for_flakiness_with_resultdb=True),
      # TODO (crbug/1456545) - This overrides the file check to ensure that we
      # have test files in the given patch. Remove when logic is removed.
      api.step_data(
          'git diff to analyze patch (2)',
          api.raw_io.stream_output('chrome/test.cc\ncomponents/file2.cc')),
      api.resultdb.query(
          current_build_sharding_test_resuts,
          ('collect tasks (with patch).'
           'ios_chrome_bookmarks_eg2tests_module_iPad Air 2 14.4 results'),
      ),
      api.resultdb.query_new_test_variants(
          rdb_pb2.QueryNewTestVariantsResponse(
              is_baseline_ready=True,
              new_test_variants=[
                  rdb_pb2.QueryNewTestVariantsResponse.NewTestVariant(
                      test_id=ios_test1,
                      variant_hash=ios_vh,
                  ),
              ]),
          step_name=('searching_for_new_tests with ResultDB.'
                     'query_new_test_variants')),
      api.post_process(
          post_process.StepCommandContains,
          ('searching_for_new_tests with ResultDB.query_new_test_variants'), [
              'rdb',
              'rpc',
              'luci.resultdb.v1.ResultDB',
              'QueryNewTestVariants',
          ]),
      api.post_process(
          post_process.MustRun,
          "test new tests for flakiness.test_pre_run (check flakiness shard #0)"
      ),
      api.post_process(
          post_process.MustRun,
          "test new tests for flakiness.test_pre_run (check flakiness shard #1)"
      ),
      api.post_process(post_process.DropExpectation),
  )

  ################################ SCRIPTS TEST ################################

  script_test_spec = {
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
  }

  script_test_id = 'check_network_annotations'
  script_v = _generate_variant(
      os='Ubuntu-16', test_suite='check_network_annotations')
  script_vh = _generate_variant_hash(script_v)
  current_build_script_test_results = {
      'invocations/build:8945511751514863184':
          api.resultdb.Invocation(test_results=[
              _generate_test_result(
                  test_id=script_test_id,
                  variant_hash=script_vh,
              )
          ])
  }

  yield api.test(
      'e2e: new android scripts tests',
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
      api.chromium_tests.read_targets_spec('fake-group', script_test_spec),
      api.flakiness(check_for_flakiness_with_resultdb=True),
      # TODO (crbug/1456545) - This overrides the file check to ensure that we
      # have test files in the given patch. Remove when logic is removed.
      api.step_data(
          'git diff to analyze patch (2)',
          api.raw_io.stream_output('chrome/test.cc\ncomponents/file2.cc')),
      api.resultdb.query(
          current_build_script_test_results,
          'check_network_annotations results',
      ),
      api.resultdb.query_new_test_variants(
          rdb_pb2.QueryNewTestVariantsResponse(
              is_baseline_ready=True,
              new_test_variants=[
                  rdb_pb2.QueryNewTestVariantsResponse.NewTestVariant(
                      test_id=script_test_id,
                      variant_hash=script_vh,
                  ),
              ]),
          step_name=('searching_for_new_tests with ResultDB.'
                     'query_new_test_variants')),
      api.post_process(
          post_process.StepCommandContains,
          ('searching_for_new_tests with ResultDB.query_new_test_variants'), [
              'rdb',
              'rpc',
              'luci.resultdb.v1.ResultDB',
              'QueryNewTestVariants',
          ]),
      api.post_process(
          post_process.MustRun,
          ('test new tests for flakiness.check_network_annotations '
           '(check flakiness shard #0)'),
      ),
      api.post_process(post_process.DropExpectation),
  )

  ################################ GTEST TEST ##################################

  gtest_test_spec = {
      'fake-android-builder': {
          'isolated_scripts': [{
              "test": "base_unittests",
              "name": "base_unittests",
              "test_id_prefix": "ninja://base:base_unittests/",
          },],
      },
  }
  gtest_v = _generate_variant(os='Ubuntu-16', test_suite='base_unittests')
  gtest_vh = _generate_variant_hash(gtest_v)
  gtest_test_id = 'ninja://base:base_unittests/TestSuite.test_d'
  gtest_pre_test_id = 'ninja://base:base_unittests/TestSuite.PRE_test_d'

  current_build_gtest_test_results = {
      'invocations/build:8945511751514863184':
          api.resultdb.Invocation(test_results=[
              _generate_test_result(
                  test_id=gtest_test_id, variant_hash=gtest_vh),
              _generate_test_result(
                  test_id=gtest_pre_test_id, variant_hash=gtest_vh),
          ])
  }

  yield api.test(
      'e2e: new android gtest tests',
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
      api.chromium_tests.read_targets_spec('fake-group', gtest_test_spec),
      api.flakiness(check_for_flakiness_with_resultdb=True),
      # TODO (crbug/1456545) - This overrides the file check to ensure that we
      # have test files in the given patch. Remove when logic is removed.
      api.step_data(
          'git diff to analyze patch (2)',
          api.raw_io.stream_output('chrome/test.cc\ncomponents/file2.cc')),
      api.resultdb.query(
          current_build_gtest_test_results,
          'base_unittests results',
      ),
      api.resultdb.query_new_test_variants(
          rdb_pb2.QueryNewTestVariantsResponse(
              is_baseline_ready=True,
              new_test_variants=[
                  rdb_pb2.QueryNewTestVariantsResponse.NewTestVariant(
                      test_id=gtest_test_id,
                      variant_hash=gtest_vh,
                  ),
              ]),
          step_name=('searching_for_new_tests with ResultDB.'
                     'query_new_test_variants')),
      api.post_process(
          post_process.StepCommandContains,
          ('searching_for_new_tests with ResultDB.query_new_test_variants'), [
              'rdb',
              'rpc',
              'luci.resultdb.v1.ResultDB',
              'QueryNewTestVariants',
          ]),
      api.post_process(post_process.StepCommandContains, (
          'test new tests for flakiness.base_unittests '
          '(check flakiness shard #0)'
      ), ('--isolated-script-test-filter=TestSuite.test_d::TestSuite.PRE_test_d'
         )),
      api.post_process(post_process.DropExpectation),
  )

  ################################ TRIM TESTS ##################################

  # reuse gtest test specs
  base_gtest_id = 'ninja://base:base_unittests/TestSuite.test_'

  current_build_gtest_test_results = {
      'invocations/build:8945511751514863184':
          api.resultdb.Invocation(test_results=[
              _generate_test_result(
                  test_id=base_gtest_id + '1', variant_hash=gtest_vh),
              _generate_test_result(
                  test_id=base_gtest_id + '2', variant_hash=gtest_vh),
              _generate_test_result(
                  test_id=base_gtest_id + '3', variant_hash=gtest_vh),
          ])
  }

  yield api.test(
      'e2e: trim tests to 1',
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
      api.chromium_tests.read_targets_spec('fake-group', gtest_test_spec),
      api.flakiness(
          check_for_flakiness_with_resultdb=True,
          max_test_targets=1,
      ),
      # TODO (crbug/1456545) - This overrides the file check to ensure that we
      # have test files in the given patch. Remove when logic is removed.
      api.step_data(
          'git diff to analyze patch (2)',
          api.raw_io.stream_output('chrome/test.cc\ncomponents/file2.cc')),
      api.resultdb.query(
          current_build_gtest_test_results,
          'base_unittests results',
      ),
      api.resultdb.query_new_test_variants(
          rdb_pb2.QueryNewTestVariantsResponse(
              is_baseline_ready=True,
              new_test_variants=[
                  rdb_pb2.QueryNewTestVariantsResponse.NewTestVariant(
                      test_id=base_gtest_id + '1',
                      variant_hash=gtest_vh,
                  ),
                  rdb_pb2.QueryNewTestVariantsResponse.NewTestVariant(
                      test_id=base_gtest_id + '2',
                      variant_hash=gtest_vh,
                  ),
                  rdb_pb2.QueryNewTestVariantsResponse.NewTestVariant(
                      test_id=base_gtest_id + '3',
                      variant_hash=gtest_vh,
                  ),
              ]),
          step_name=('searching_for_new_tests with ResultDB.'
                     'query_new_test_variants')),
      api.post_process(
          post_process.StepCommandContains,
          ('searching_for_new_tests with ResultDB.query_new_test_variants'), [
              'rdb',
              'rpc',
              'luci.resultdb.v1.ResultDB',
              'QueryNewTestVariants',
          ]),
      api.post_process(post_process.MustRun, 'randomly sampling 1 tests'),
      api.post_process(
          post_process.MustRunRE,
          ('test new tests for flakiness.base_unittests '
           '\(check flakiness shard \#0\)'),
          at_most=1),
      api.post_process(post_process.DropExpectation),
  )

  ios_test_spec_skip = {
      'fake-builder': {
          'isolated_scripts': [{
              'test': 'ios_chrome_bookmarks_eg2tests_module',
              'name': ('ios_chrome_bookmarks_eg2tests_module_iPad Air 2 14.4'),
              'swarming': {
                  'dimensions': {
                      'os': 'Mac-11'
                  },
                  'shards': 2,
              },
              'test_id_prefix': (
                  'ninja://ios/chrome/test/earl_grey2:ios_chrome_bookmarks_eg2tests_module/'
              ),
              'check_flakiness_for_new_tests': False,
          },],
      },
  }

  yield api.test(
      'check_flakiness_for_new_tests_false',
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
          }),
      ),
      api.chromium_tests.read_targets_spec('fake-group', ios_test_spec_skip),
      api.flakiness(check_for_flakiness_with_resultdb=True),
      api.step_data(
          'git diff to analyze patch (2)',
          api.raw_io.stream_output('chrome/test.cc\ncomponents/file2.cc'),
      ),
      api.resultdb.query_new_test_variants(
          rdb_pb2.QueryNewTestVariantsResponse(
              is_baseline_ready=True,
              new_test_variants=[
                  rdb_pb2.QueryNewTestVariantsResponse.NewTestVariant(
                      test_id=ios_test1,
                      variant_hash=ios_vh,
                  ),
              ],
          ),
          step_name=(
              'searching_for_new_tests with ResultDB.query_new_test_variants'),
      ),
      api.post_process(
          post_process.DoesNotRun,
          'test new tests for flakiness',
      ),
      api.post_process(post_process.DropExpectation),
  )
