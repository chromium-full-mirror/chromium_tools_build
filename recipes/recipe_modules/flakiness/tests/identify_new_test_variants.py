# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process
from PB.go.chromium.org.luci.buildbucket.proto import build as build_pb2
from PB.go.chromium.org.luci.buildbucket.proto \
  import builder_common as builder_common_pb2
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb2
from PB.go.chromium.org.luci.resultdb.proto.v1 import resultdb as rdb_pb2

DEPS = [
    'flakiness',
    'recipe_engine/buildbucket',
    'recipe_engine/resultdb',
]


def RunSteps(api):
  api.flakiness.identify_new_test_variants()


def GenTests(api):
  basic_build = build_pb2.Build(
      builder=builder_common_pb2.BuilderID(
          builder='Builder', project='chromium', bucket='try'),
      infra=build_pb2.BuildInfra(
          resultdb=build_pb2.BuildInfra.ResultDB(invocation='invocations/100')),
      input=build_pb2.Build.Input(
          gerrit_changes=[
              common_pb2.GerritChange(
                  host='chromium-review.googlesource.com',
                  project='chromium/src',
                  change=10,
                  patchset=3,
              )
          ],))

  yield api.test(
      'basic',
      api.buildbucket.build(basic_build),
      api.flakiness(check_for_flakiness=True),
      api.resultdb.query_new_test_variants(
          rdb_pb2.QueryNewTestVariantsResponse(
              is_baseline_ready=True,
              new_test_variants=[
                  rdb_pb2.QueryNewTestVariantsResponse.NewTestVariant(
                      test_id="sample:12345",
                      variant_hash="12345",
                  )
              ]),
          step_name='searching_for_new_tests with ResultDB.query_new_test_variants'
      ),
      api.post_process(
          post_process.StepCommandContains,
          ('searching_for_new_tests with ResultDB.query_new_test_variants'), [
              'rdb',
              'rpc',
              'luci.resultdb.v1.ResultDB',
              'QueryNewTestVariants',
          ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'baseline not ready',
      api.buildbucket.build(basic_build),
      api.flakiness(check_for_flakiness=True),
      api.resultdb.query_new_test_variants(
          rdb_pb2.QueryNewTestVariantsResponse(
              is_baseline_ready=False, new_test_variants=[]),
          step_name='searching_for_new_tests with ResultDB.query_new_test_variants'
      ),
      api.post_process(
          post_process.MustRun,
          'searching_for_new_tests with ResultDB.'
          'Baseline is not yet ready to calculate new tests',
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'no tests',
      api.buildbucket.build(basic_build),
      api.flakiness(check_for_flakiness=False),
      api.resultdb.query_new_test_variants(
          rdb_pb2.QueryNewTestVariantsResponse(
              is_baseline_ready=True, new_test_variants=[]),
          step_name='searching_for_new_tests with ResultDB.query_new_test_variants'
      ),
      api.post_process(
          post_process.MustRun,
          'searching_for_new_tests with ResultDB.'
          'No new tests detected',
      ),
      api.post_process(post_process.DropExpectation),
  )
