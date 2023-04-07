# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process
from recipe_engine.recipe_api import StepFailure

from RECIPE_MODULES.build.chromium_tests import steps

DEPS = [
    'chromium',
    'chromium_android',
    'chromium_checkout',
    'chromium_tests',
    'depot_tools/gclient',
    'recipe_engine/assertions',
    'recipe_engine/buildbucket',
    'recipe_engine/json',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/step',
    'test_utils',
]


def RunSteps(api):
  api.gclient.set_config('chromium')
  api.gclient.apply_config('android')
  api.chromium.set_config('chromium', TARGET_PLATFORM='android')
  api.chromium_android.set_config('main_builder')
  api.chromium_checkout.ensure_checkout()

  test_spec = steps.AndroidJunitTestSpec.create(
      'test_name',
      target_name=api.properties.get('target_name'),
  )
  test = test_spec.get_test(api.chromium_tests)

  api.assertions.assertEqual(test.option_flags, steps.TestOptionFlags.create())
  api.chromium.compile(targets=test.compile_targets(), name='compile')

  try:
    _, invalid_suites, failed_suites = api.test_utils.run_tests_once([test], '')
    if not invalid_suites:
      assert test.has_valid_results('')
  finally:
    api.step('details', [])
    api.step.active_result.presentation.logs['details'] = [
        'compile_targets: {!r}'.format(test.compile_targets()),
        'failures: {!r}'.format(test.failures('')),
        'uses_local_devices: {!r}'.format(test.uses_local_devices),
    ]
  if invalid_suites or failed_suites:
    raise StepFailure('failure in ' + test.name)


def GenTests(api):

  def calls_runner_script(check, step_odict, step, runner_script_name):
    cmd = step_odict[step].cmd
    check(
        'In step %s, runner script %s is wrapped with rdb' %
        (step, runner_script_name), cmd[0] == 'rdb')
    runner_script = cmd[cmd.index('--') + 1]
    check('step %s called runner script %s' % (step, runner_script_name),
          runner_script.endswith('bin/%s' % runner_script_name))

  yield api.test(
      'basic',
      api.chromium.ci_build(
          builder_group='test_group',
          builder='test_buildername',
      ),
      api.post_process(post_process.MustRun, 'test_name'),
      api.post_process(calls_runner_script, 'test_name', 'run_test_name'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'failure',
      api.chromium.ci_build(
          builder_group='test_group',
          builder='test_buildername',
      ),
      api.override_step_data(
          'test_name results',
          stdout=api.raw_io.output_text(
              api.test_utils.rdb_results(
                  'test_name', failing_tests=['Test.One']))),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'invalid',
      api.chromium.ci_build(
          builder_group='test_group',
          builder='test_buildername',
      ),
      # No failing tests in RDB results, but a non-zero exit code will make
      # the recipe consider the suite's results invalid.
      api.override_step_data('test_name', retcode=1),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'different-target-name',
      api.chromium.ci_build(
          builder_group='test_group',
          builder='test_buildername',
      ),
      api.properties(target_name='target_name'),
      api.post_process(post_process.StepCommandContains, 'compile',
                       ['target_name']),
      api.post_process(post_process.MustRun, 'test_name'),
      api.post_process(calls_runner_script, 'test_name', 'run_target_name'),
      api.post_process(post_process.DropExpectation),
  )
