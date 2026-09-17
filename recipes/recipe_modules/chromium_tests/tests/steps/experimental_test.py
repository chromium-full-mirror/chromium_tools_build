# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium, chromium_tests
from RECIPE_MODULES.depot_tools import tryserver
from RECIPE_MODULES.recipe_engine import (
  assertions,
  buildbucket,
  path,
  properties,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  buildbucket: buildbucket.API
  chromium: chromium.API
  chromium_tests: chromium_tests.API
  path: path.API
  properties: properties.API
  step: step.API
  tryserver: tryserver.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API
  properties: properties.TEST_API


from recipe_engine import post_process

from RECIPE_MODULES.build.chromium_tests import steps


def RunSteps(api: DEPS):

  class RecordingTestSpec(steps.TestWrapperSpec):
    @property
    def test_wrapper_class(self):
      return RecordingTest

  class RecordingTest(steps.TestWrapper):
    """Records execution of the has_valid_results and failures methods.

    When a call is made to either method, a no-op step is created with
    the name of the method followed by the name of the test and the
    suffix.
    """

    def has_valid_results(self, suffix):
      api.step('has_valid_results {}'.format(self.step_name(suffix)), [])
      return super().has_valid_results(suffix)

    def deterministic_failures(self, suffix):
      api.step('failures {}'.format(self.step_name(suffix)), [])
      return super().deterministic_failures(suffix)

  option_flags = steps.TestOptionFlags.create(
    filter_flag='--filter-flag',
    filter_delimiter='|',
    repeat_flag='--repeat-flag',
    retry_limit_flag='--retry-limit-flag',
    run_disabled_flag='--run-disabled-flag',
    batch_limit_flag='--batch-limit-flag',
  )
  mock_test_spec = steps.MockTestSpec.create(
    'inner_test',
    has_valid_results=api.properties.get('has_valid_results', True),
    failures=api.properties.get('failures'),
    option_flags=option_flags,
  )
  recording_test_spec = RecordingTestSpec.create(mock_test_spec)
  experimental_test_spec = steps.ExperimentalTestSpec.create(
    recording_test_spec,
    experiment_percentage=api.properties['experiment_percentage'],
  )

  experiment_on = api.properties['experiment_percentage'] == 100
  experimental_test = experimental_test_spec.get_test(api.chromium_tests)

  api.assertions.assertEqual(
    experimental_test.step_name(''), 'inner_test (experimental)'
  )
  api.assertions.assertEqual(
    experimental_test.step_name('foo'), 'inner_test (foo, experimental)'
  )

  api.assertions.assertEqual(experimental_test.option_flags, option_flags)

  api.step.empty('Configured experimental test %s' % experimental_test.name)

  suffix = api.properties.get('suffix', '')

  checkout_dir = api.path.start_dir
  source_dir = checkout_dir / 'fake-repo'
  build_dir = source_dir / 'out' / 'some_build_dir'

  experimental_test.pre_run(suffix)
  experimental_test.run(checkout_dir, source_dir, build_dir, suffix)

  if not experiment_on:
    return

  # Just for code coverage.
  experimental_test.get_invocation_names(suffix)
  experimental_test.update_rdb_results(suffix, {})

  assert experimental_test.has_valid_results('')
  assert not experimental_test.deterministic_failures('')


def GenTests(api: TEST_DEPS):

  yield api.test(
    'experiment_on',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.properties(experiment_percentage=100),
    api.post_process(post_process.MustRun, 'pre_run inner_test (experimental)'),
    api.post_process(
      post_process.StepTextContains,
      'inner_test (experimental)',
      ['This is an experimental test that was selected for this build'],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'experiment_off',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.properties(experiment_percentage=0),
    api.post_process(
      post_process.StepCommandEmpty, 'inner_test (experimental)'
    ),
    api.post_process(
      post_process.StepTextContains,
      'inner_test (experimental)',
      ['This test was not selected for its experiment in this build'],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'experiment_on_invalid_results',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.properties(experiment_percentage=100, has_valid_results=False),
    api.post_process(
      post_process.MustRun, 'has_valid_results inner_test (experimental)'
    ),
    api.post_process(
      post_process.DoesNotRun, 'failures inner_test (experimental)'
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'experiment_off_invalid_results',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.properties(experiment_percentage=0, has_valid_results=False),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'experiment_on_valid_failures',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.properties(experiment_percentage=100, failures=['foo']),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'experiment_off_valid_failures',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.properties(experiment_percentage=0, failures=['foo']),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'failure_in_pre_run',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.properties(experiment_percentage=100),
    api.override_step_data('pre_run inner_test (experimental)', retcode=1),
    api.post_process(post_process.MustRun, 'pre_run inner_test (experimental)'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'failure_in_run',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.properties(experiment_percentage=100),
    api.override_step_data('inner_test (experimental)', retcode=1),
    api.post_process(post_process.MustRun, 'inner_test (experimental)'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'with_patch',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.properties(experiment_percentage=100, suffix='with patch'),
    api.post_process(
      post_process.MustRun, 'pre_run inner_test (with patch, experimental)'
    ),
    api.post_process(
      post_process.MustRun, 'inner_test (with patch, experimental)'
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'experiment_key_on_triggered_tester',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_tester',
      parent_buildername='test_parent',
    ),
    api.properties(experiment_percentage=10),
    api.post_process(
      post_process.StepTextContains,
      'inner_test (experimental)',
      ['This is an experimental test that was selected for this build'],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'experiment_key_on_compilator',
    api.chromium.try_build(
      builder_group='test_group',
      builder='test_compilator',
    ),
    api.properties(
      experiment_percentage=50,
      orchestrator={
        'builder_name': 'test_orchestrator',
        'builder_group': 'test_group',
      },
    ),
    api.post_process(
      post_process.StepTextContains,
      'inner_test (experimental)',
      ['This is an experimental test that was selected for this build'],
    ),
    api.post_process(post_process.DropExpectation),
  )
