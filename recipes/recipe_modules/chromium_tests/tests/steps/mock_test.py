# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium_tests
from RECIPE_MODULES.recipe_engine import (
    assertions,
    path,
    properties,
    step,
)


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  chromium_tests: chromium_tests.API
  path: path.API
  properties: properties.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium_tests: chromium_tests.TEST_API
  properties: properties.TEST_API

from recipe_engine import post_process

from RECIPE_MODULES.build.chromium_tests import steps


def RunSteps(api: DEPS):
  test_spec = steps.MockTestSpec.create(
      name=api.properties.get('test_name', 'MockTest'),
      runs_on_swarming=api.properties.get('runs_on_swarming', True),
      shards=4)
  test = test_spec.get_test(api.chromium_tests)

  test.pre_run('', is_ci_only=api.properties.get('is_ci_only', False))

  checkout_dir = api.path.start_dir
  source_dir = checkout_dir / 'fake-repo'
  build_dir = source_dir / 'out' / 'some_build_dir'
  try:
    test.run(checkout_dir, source_dir, build_dir, '')
  except api.step.InfraFailure:
    api.step.empty('infra failure in %s' % test.name)
  except api.step.StepFailure:
    api.step.empty('step failure in %s' % test.name)

  if test.runs_on_swarming:
    task = test.get_task('')
    api.assertions.assertEqual(len(task.get_task_ids()), 4)

  api.assertions.assertEqual(test.isolate_profile_data, False)

  test.raw_cmd = ['raw-cmd']
  api.assertions.assertEqual(test.raw_cmd, ['raw-cmd'])

  test.relative_cwd = 'relative-cwd'
  api.assertions.assertEqual(test.relative_cwd, 'relative-cwd')

  api.assertions.assertEqual(test.retry_only_failed_tests, True)


def GenTests(api: TEST_DEPS):
  failure_code = steps.MockTest.ExitCodes.FAILURE
  infra_code = steps.MockTest.ExitCodes.INFRA_FAILURE

  yield api.test(
      'basic',
      api.post_process(post_process.MustRun, 'pre_run MockTest'),
      api.post_process(post_process.MustRun, 'MockTest'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'is_ci_only',
      api.properties(is_ci_only=True),
      api.post_process(post_process.MustRun, 'pre_run MockTest'),
      api.post_process(post_process.MustRun, 'MockTest'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'failure',
      api.properties(test_name='base_unittests'),
      api.chromium_tests.override_step_data(
          'base_unittests', retcode=failure_code),
      api.post_process(post_process.MustRun, 'step failure in base_unittests'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'infra_failure',
      api.properties(test_name='base_unittests'),
      api.chromium_tests.override_step_data(
          'base_unittests', retcode=infra_code),
      api.post_process(post_process.MustRun, 'infra failure in base_unittests'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'local',
      api.properties(runs_on_swarming=False),
      api.post_process(post_process.MustRun, 'pre_run MockTest'),
      api.post_process(post_process.MustRun, 'MockTest'),
      api.post_process(post_process.DropExpectation),
  )
