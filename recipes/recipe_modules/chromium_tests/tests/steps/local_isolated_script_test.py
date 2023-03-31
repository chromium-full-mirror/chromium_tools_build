# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process
from recipe_engine.recipe_api import StepFailure

from RECIPE_MODULES.build.chromium_tests import steps

DEPS = [
    'chromium',
    'chromium_tests',
    'isolate',
    'recipe_engine/assertions',
    'recipe_engine/buildbucket',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/resultdb',
    'recipe_engine/step',
    'test_utils',
]


def RunSteps(api):
  test_name = api.properties.get('test_name') or 'base_unittests'

  isolate_coverage_data = api.properties.get('isolate_coverage_data', False)

  resultdb = steps.ResultDB(result_format='json')
  test_spec = steps.LocalIsolatedScriptTestSpec.create(
      test_name,
      override_compile_targets=api.properties.get('override_compile_targets'),
      isolate_coverage_data=isolate_coverage_data,
      resultdb=resultdb)

  test = test_spec.get_test(api.chromium_tests)

  assert not test.runs_on_swarming

  test_repeat_count = api.properties.get('repeat_count')
  if test_repeat_count:
    test.test_options = steps.TestOptions.create(
        test_filter=api.properties.get('test_filter'),
        repeat_count=test_repeat_count,
        retry_limit=0,
        run_disabled=bool(test_repeat_count))

  raw_cmd = api.properties.get('raw_cmd') or []
  if raw_cmd:
    test.raw_cmd = list(raw_cmd)

  relative_cwd = api.properties.get('relative_cwd') or None
  if relative_cwd:
    test.relative_cwd = relative_cwd

  try:
    _, invalid_suites, failed_suites = api.test_utils.run_tests_once([test], '')
  finally:
    api.step('details', [])
    api.step.active_result.presentation.logs['details'] = [
        'compile_targets: %r' % test.compile_targets(),
        'isolate_target: %r' % test.isolate_target,
        'uses_local_devices: %r' % test.uses_local_devices,
        'uses_isolate: %r' % test.uses_isolate,
    ]

    if 'expected_pass_fail_counts' in api.properties:
      api.assertions.assertEqual(
          test.pass_fail_counts(''),
          api.properties['expected_pass_fail_counts'])

  if invalid_suites or failed_suites:
    raise StepFailure('failure in ' + test.name)


def GenTests(api):
  def verify_isolate_flag(check, step_odict):
    step = step_odict[
        'base_unittests']
    check(
        'LLVM_PROFILE_FILE=${ISOLATED_OUTDIR}/profraw/default-%1m.profraw'
        in step.cmd)

  yield api.test(
      'basic',
      api.chromium.ci_build(
          builder_group='test_group',
          builder='test_buildername',
      ),
      api.properties(swarm_hashes={
          'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff',
      }),
      api.post_process(post_process.StepCommandContains, 'base_unittests',
                       ['rdb']),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'failure',
      api.chromium.ci_build(
          builder_group='test_group',
          builder='test_buildername',
      ),
      api.properties(swarm_hashes={
          'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff',
      }),
      api.override_step_data(
          'base_unittests results',
          stdout=api.raw_io.output_text(
              api.test_utils.rdb_results(
                  'base_unittests', failing_tests=['Test.One']))),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'raw_cmd',
      api.chromium.ci_build(
          builder_group='test_group',
          builder='test_buildername',
      ),
      api.properties(
          swarm_hashes={
              'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff',
          },
          raw_cmd=['./base_unittests', '--bar'],
          relative_cwd='out/Release'),
      api.post_process(post_process.StepCommandContains, 'base_unittests', [
          '--relative-cwd', 'out/Release', '--', './base_unittests', '--bar',
          '--isolated-script-test-output'
      ]),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'override_compile_targets',
      api.chromium.ci_build(
          builder_group='test_group',
          builder='test_buildername',
      ),
      api.properties(
          swarm_hashes={
              'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff',
          },
          override_compile_targets=['base_unittests_run']),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'pass_fail_counts',
      api.chromium.ci_build(
          builder_group='test_group',
          builder='test_buildername',
      ),
      api.properties(
          swarm_hashes={
              'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff',
          },
          expected_pass_fail_counts={},
      ),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'customized_test_options',
      api.chromium.ci_build(
          builder_group='test_group',
          builder='test_buildername',
      ),
      api.properties(
          swarm_hashes={
              'blink_web_tests': 'ffffffffffffffffffffffffffffffffffffffff',
          },
          test_filter=['test1', 'test2'],
          repeat_count=20,
          test_name='blink_web_tests'),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'isolate_coverage_data',
      api.chromium.ci_build(
          builder_group='test_group',
          builder='test_buildername',
      ),
      api.properties(
          isolate_coverage_data=True,
          swarm_hashes={
              'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff',
          }),
      api.post_process(verify_isolate_flag),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )
