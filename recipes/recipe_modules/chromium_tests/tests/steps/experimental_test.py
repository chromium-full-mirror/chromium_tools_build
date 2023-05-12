# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

DEPS = [
    'chromium',
    'chromium_tests',
    'depot_tools/tryserver',
    'recipe_engine/assertions',
    'recipe_engine/buildbucket',
    'recipe_engine/properties',
    'recipe_engine/step',
]

from recipe_engine import post_process

from RECIPE_MODULES.build.chromium_tests import steps


def RunSteps(api):

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

    def failures(self, suffix):
      api.step('failures {}'.format(self.step_name(suffix)), [])
      return super().failures(suffix)

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
      option_flags=option_flags)
  recording_test_spec = RecordingTestSpec.create(mock_test_spec)
  experimental_test_spec = steps.ExperimentalTestSpec.create(
      recording_test_spec,
      experiment_percentage=api.properties['experiment_percentage'],
      api=api)

  experiment_on = api.properties['experiment_percentage'] == '100'
  api.assertions.assertNotEqual(experiment_on,
                                bool(experimental_test_spec.disabled_reason))
  if not experiment_on:
    return

  experimental_test = experimental_test_spec.get_test(api.chromium_tests)

  api.assertions.assertEqual(experimental_test.option_flags, option_flags)

  api.step.empty('Configured experimental test %s' % experimental_test.name)

  suffix = api.properties.get('suffix', '')

  experimental_test.pre_run(suffix)
  experimental_test.run(suffix)

  # Just for code coverage.
  experimental_test.get_invocation_names(suffix)
  experimental_test.update_rdb_results(suffix, {})

  assert experimental_test.has_valid_results('')
  assert not experimental_test.failures('')
  assert not experimental_test.deterministic_failures('')

  experimental_test_spec = experimental_test_spec.add_info_message(
      'This is an experimental test')
  api.assertions.assertEqual(
      experimental_test_spec.test_spec.test_spec.info_messages,
      ('This is an experimental test',))


def GenTests(api):

  yield api.test(
      'experiment_on',
      api.chromium.ci_build(
          builder_group='test_group',
          builder='test_buildername',
      ),
      api.properties(experiment_percentage='100'),
      api.post_process(post_process.MustRun,
                       'pre_run inner_test (experimental)'),
      api.post_process(post_process.MustRun, 'inner_test (experimental)'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'experiment_off',
      api.chromium.ci_build(
          builder_group='test_group',
          builder='test_buildername',
      ),
      api.properties(experiment_percentage='0'),
      api.post_process(post_process.DoesNotRun,
                       'pre_run inner_test (experimental)'),
      api.post_process(post_process.DoesNotRun, 'inner_test (experimental)'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'experiment_on_invalid_results',
      api.chromium.ci_build(
          builder_group='test_group',
          builder='test_buildername',
      ),
      api.properties(experiment_percentage='100', has_valid_results=False),
      api.post_process(post_process.MustRun,
                       'has_valid_results inner_test (experimental)'),
      api.post_process(post_process.DoesNotRun,
                       'failures inner_test (experimental)'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'experiment_off_invalid_results',
      api.chromium.ci_build(
          builder_group='test_group',
          builder='test_buildername',
      ),
      api.properties(experiment_percentage='0', has_valid_results=False),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'experiment_on_valid_failures',
      api.chromium.ci_build(
          builder_group='test_group',
          builder='test_buildername',
      ),
      api.properties(experiment_percentage='100', failures=['foo']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'experiment_off_valid_failures',
      api.chromium.ci_build(
          builder_group='test_group',
          builder='test_buildername',
      ),
      api.properties(experiment_percentage='0', failures=['foo']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'failure_in_pre_run',
      api.chromium.ci_build(
          builder_group='test_group',
          builder='test_buildername',
      ),
      api.properties(experiment_percentage='100'),
      api.override_step_data('pre_run inner_test (experimental)', retcode=1),
      api.post_process(post_process.MustRun,
                       'pre_run inner_test (experimental)'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'failure_in_run',
      api.chromium.ci_build(
          builder_group='test_group',
          builder='test_buildername',
      ),
      api.properties(experiment_percentage='100'),
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
      api.properties(experiment_percentage='100', suffix='with patch'),
      api.post_process(post_process.MustRun,
                       'pre_run inner_test (with patch, experimental)'),
      api.post_process(post_process.MustRun,
                       'inner_test (with patch, experimental)'),
      api.post_process(post_process.DropExpectation),
  )
