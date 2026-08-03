# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process
from PB.go.chromium.org.luci.resultdb.proto.v1 import (
    common as common_pb2,
    invocation as invocation_pb2,
    test_result as test_result_pb2,
)
from RECIPE_MODULES.build.devtools.commons import SwarmingTrigger
from RECIPE_MODULES.build.devtools.unit_tests_runner import UnitTests
from RECIPE_MODULES.build.devtools.performance_tests_runner import PerformanceTests
from RECIPE_MODULES.build.devtools.test_phases import run_test_pipelines
from RECIPE_MODULES.build.devtools.test_runner_base import (
    FLAKE_DETECTION_SKIPPED_TESTS_PATTERN_FOOTER,
    FLAKE_DETECTION_SKIPPED_TESTS_FOOTER,
)

DEPS = [
    'devtools',
    'chromium',
    'chromium_swarming',
    'depot_tools/tryserver',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/futures',
    'recipe_engine/json',
    'recipe_engine/properties',
    'recipe_engine/step',
    'recipe_engine/raw_io',
    'recipe_engine/resultdb',
    'recipe_engine/path',
    'recipe_engine/platform',
    'v8',
]


def RunSteps(api):
  api.devtools.configure(
      'Release', is_official_build=False, devtools_skip_typecheck=True)
  api.devtools.update()
  trigger = SwarmingTrigger(api, '1234567/890')

  runners = [
      UnitTests(
          api, trigger, 'Release', coverage=False, step_name='Unit Tests'),
      PerformanceTests(api, trigger, 'Release', step_name='Performance Tests'),
  ]
  results = run_test_pipelines(api, runners)
  return results.raw_result()


def GenTests(api):
  git_repo = 'https://chromium.googlesource.com/devtools/devtools-frontend'

  def try_build(builder='linux'):
    return api.buildbucket.try_build(
        project='devtools',
        builder=builder,
        git_repo=git_repo,
        change_number=91827,
        patch_set=1)

  def test_result(test_id, test_type, expected=False):
    return test_result_pb2.TestResult(
        test_id=test_id,
        name=f'name {test_id}',
        expected=expected,
        status=test_result_pb2.FAIL if not expected else test_result_pb2.PASS,
        tags=[common_pb2.StringPair(key="test_type", value=test_type)],
    )

  def rdb_query(step_name, *results):
    return api.resultdb.query(
        {
            'task-example.swarmingserver.appspot.com-some-task-id':
                api.resultdb.Invocation(test_results=results)
        },
        step_name=step_name,
    )

  yield api.test('basic_pipeline', try_build())

  # Coverage for skip pattern footers
  yield api.test(
      'trybot_skip_pattern',
      try_build(),
      api.override_step_data(
          'find new tests.git diff',
          stdout=api.raw_io.output_text(
              'front_end/foo.test.ts\nfront_end/bar.skip.test.ts')),
      api.override_step_data(
          'find new tests.parse description',
          api.json.output({
              FLAKE_DETECTION_SKIPPED_TESTS_PATTERN_FOOTER: ['*.skip.test.ts'],
              FLAKE_DETECTION_SKIPPED_TESTS_FOOTER: ['front_end/other.test.ts'],
          })),
      api.post_process(post_process.DropExpectation),
  )

  # Coverage for r.expected=True in ResultDB query
  yield api.test(
      'rdb_passing_test',
      try_build(),
      rdb_query(
          'Pipeline Unit Tests.rdb query for unit_tests',
          test_result(
              'front_end/foo.test.ts:my_test', 'unit_tests', expected=True)),
      api.post_process(post_process.DropExpectation),
  )

  # Coverage for exoneration failure setting presentation.step_text
  yield api.test(
      'exoneration_fails',
      try_build(),
      rdb_query(
          'Pipeline Unit Tests.rdb query for unit_tests',
          test_result(
              'front_end/foo.test.ts:my_test', 'unit_tests', expected=False)),
      api.step_data(
          'Pipeline Unit Tests.Flake exoneration attempt.Unit Tests (rerun).'
          'Unit Tests (rerun) shards results.Unit Tests (rerun) (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(
              None, {'shards': [{
                  'state': 'COMPLETED (FAILURE)'
              }]}),
      ),
      api.post_process(post_process.DropExpectation),
      status='FAILURE',
  )

  # Verifies that unowned touched tests emit a warning step during Flake Detection.
  yield api.test(
      'unowned_touched_test_warning',
      try_build(),
      api.override_step_data(
          'find new tests.git diff',
          stdout=api.raw_io.output_text(
              'scripts/eslint_rules/tests/foo_test.ts')),
      api.post_process(post_process.MustRun,
                       'Unowned touched test files skipped in Flake Detection'),
      api.post_process(
          post_process.DoesNotRun,
          'Pipeline Unit Tests.Detect flakes in new tests.Trigger Unit Tests (flake detection)'
      ),
      api.post_process(post_process.DropExpectation),
  )

  # Verifies that non-exonerable runners do not trigger exoneration steps.
  yield api.test(
      'non_exonerable_runner_no_exoneration',
      try_build(),
      rdb_query('Pipeline Performance Tests.rdb query for perf_tests',
                test_result('perf1', 'perf_tests', expected=False)),
      # Verifies that PerformanceTests (non-exonerable) does not create an exoneration step
      api.post_process(post_process.DoesNotRun,
                       'Pipeline Performance Tests.Flake exoneration attempt'),
      api.post_process(post_process.DropExpectation),
  )
