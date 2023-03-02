# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

DEPS = [
    'chromium_tests',
    'chromium_tests_builder_config',
    'filter',
    'depot_tools/tryserver',
    'recipe_engine/json',
    'recipe_engine/properties',
    'recipe_engine/swarming',
]


def RunSteps(api):
  builder_id, builder_config = (
      api.chromium_tests_builder_config.lookup_builder())
  if api.tryserver.is_tryserver:
    return api.chromium_tests.trybot_steps(builder_id, builder_config)
  return api.chromium_tests.main_waterfall_steps(builder_id, builder_config)


def GenTests(api):
  builder_db = ctbc.BuilderDatabase.create({
      'test-group': {
          'test-builder':
              ctbc.BuilderSpec.create(
                  chromium_config='chromium',
                  gclient_config='chromium',
              ),
      }
  })
  try_db = ctbc.TryDatabase.create({
      'test-try-group': {
          'test-try-builder':
              ctbc.TrySpec.create_for_single_mirror(
                  builder_group='test-group',
                  buildername='test-builder',
              ),
      }
  })

  def common_test_data(test_spec):
    t = api.chromium_tests.read_targets_spec('test-group', {
        'test-builder': {
            'isolated_scripts': [test_spec],
        },
    })

    test_name = test_spec['name']
    isolate_name = test_spec.get('isolate_name') or test_name

    step_filter = post_process.Filter()
    # Any step with the test name in it
    step_filter = step_filter.include_re(
        r'.*\b{}\b'.format(test_name), at_least=0)
    # Any step with the isolate name in it
    step_filter = step_filter.include_re(
        r'.*\b{}\b'.format(isolate_name), at_least=0)
    # Any errors resulting from generating the test
    step_filter = step_filter.include_re(r'.*\bspec format error$', at_least=0)
    # The step for reporting ci_only tests
    step_filter = step_filter.include_re('ci_only tests$', at_least=0)
    # The step for reporting experimental tests that are not being run
    step_filter = step_filter.include_re(
        'experimental tests not in experiment', at_least=0)
    # The final result of the recipe
    step_filter = step_filter.include_re(r'\$result$', at_least=0)
    t += api.post_process(step_filter)

    return t

  def ci_build(test_spec, **kwargs):
    t = api.chromium_tests_builder_config.ci_build(
        builder_group='test-group',
        builder='test-builder',
        builder_db=builder_db,
        **kwargs)
    t += common_test_data(test_spec)
    return t

  def try_build(test_spec, **kwargs):
    t = api.chromium_tests_builder_config.try_build(
        builder_group='test-group',
        builder='test-builder',
        builder_db=builder_db,
        try_db=try_db,
        **kwargs)
    t += common_test_data(test_spec)
    return t

  def test_spec_format_error(s, step_name='test spec format error'):
    """Adds a post check for step named `step_name`.

    The prose contained in the details log must contain the substring
    `s`.
    """

    def check(check, steps):
      details = steps[step_name].logs['details']
      details = details.replace('\n', ' ')
      check(s in details)

    return api.post_check(check)

  yield api.test(
      'basic',
      ci_build(test_spec={
          'name': 'base_unittests',
          'isolate_name': 'base_unittests_run',
      }),
  )

  yield api.test(
      'fake_results_handler',
      ci_build(
          test_spec={
              'name': 'base_unittests',
              'isolate_name': 'base_unittests_run',
              'results_handler': 'fake',
              'swarming': {
                  'can_use_on_swarming_builders': True,
              },
          }),
  )

  yield api.test(
      'swarming',
      ci_build(
          test_spec={
              'name': 'base_unittests',
              'isolate_name': 'base_unittests_run',
              'test_id_prefix': 'ninja://chrome/test:base_unittests/',
              'merge': {
                  'script': '//path/to/script.py',
              },
              'setup': [{
                  'name': 'setup1',
                  'script': '//path/to/setup1.py'
              }, {
                  'name': 'setup2',
                  'script': '//path/to/setup2.py'
              }],
              'teardown': [{
                  'name': 'teardown1',
                  'script': '//path/to/teardown1.py'
              }, {
                  'name': 'teardown2',
                  'script': '//path/to/teardown2.py'
              }],
              'swarming': {
                  'can_use_on_swarming_builders': True,
              },
          }),
  )

  yield api.test(
      'service_account',
      ci_build(
          test_spec={
              'name': 'base_unittests',
              'isolate_name': 'base_unittests_run',
              'swarming': {
                  'can_use_on_swarming_builders': True,
                  'service_account': 'test-account@serviceaccount.com',
              },
          }),
      api.post_check(
          api.swarming.check_triggered_request,
          'test_pre_run.[trigger] base_unittests', lambda check, req: check(
              req.service_account == 'test-account@serviceaccount.com')),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'bad tear down',
      ci_build(
          test_spec={
              'name': 'base_unittests',
              'isolate_name': 'base_unittests_run',
              'merge': {
                  'script': '//path/to/script.py',
              },
              'teardown': [{
                  'name': 'teardown1',
                  'script': '//path/to/teardown1.py'
              }, {
                  'name': 'teardown2',
                  'script': 'path/to/teardown2.py'
              }],
              'swarming': {
                  'can_use_on_swarming_builders': True,
              },
          }),
      test_spec_format_error(
          'contains a custom tear down script "path/to/teardown2.py"'
          " that doesn't match the expected format"),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'bad set up',
      ci_build(
          test_spec={
              'name': 'base_unittests',
              'isolate_name': 'base_unittests_run',
              'merge': {
                  'script': '//path/to/script.py',
              },
              'setup': [{
                  'name': 'setup1',
                  'script': '//path/to/setup1.py'
              }, {
                  'name': 'setup2',
                  'script': 'path/to/setup2.py'
              }],
              'swarming': {
                  'can_use_on_swarming_builders': True,
              },
          }),
      test_spec_format_error(
          'contains a custom set up script "path/to/setup2.py"'
          " that doesn't match the expected format"),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'swarming_trigger_script',
      ci_build(
          test_spec={
              'name': 'base_unittests',
              'isolate_name': 'base_unittests_run',
              'trigger_script': {
                  'script': '//path/to/script.py',
              },
              'swarming': {
                  'can_use_on_swarming_builders': True,
              },
          }),
  )

  yield api.test(
      'swarming_trigger_script_invalid',
      ci_build(
          test_spec={
              'name': 'base_unittests',
              'isolate_name': 'base_unittests_run',
              'trigger_script': {
                  'script': 'bad',
              },
              'swarming': {
                  'can_use_on_swarming_builders': True,
              },
          }),
      test_spec_format_error('contains a custom trigger_script "bad"'
                             " that doesn't match the expected format"),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'swarming_dimension_sets',
      ci_build(
          test_spec={
              'name': 'base_unittests',
              'isolate_name': 'base_unittests_run',
              'swarming': {
                  'can_use_on_swarming_builders': True,
                  'dimension_sets': [{
                      'os': 'Linux',
                      'foo': None,
                  }],
                  'optional_dimensions': {
                      '60': {
                          'bar': 'baz',
                      },
                  },
              },
          }),
  )

  yield api.test(
      'swarming_dimension_sets_with_legacy_optional_dimensions',
      ci_build(
          test_spec={
              'name': 'base_unittests',
              'isolate_name': 'base_unittests_run',
              'swarming': {
                  'can_use_on_swarming_builders': True,
                  'optional_dimensions': {
                      '60': [{
                          'bar': 'baz',
                      }],
                  },
              },
          }),
  )

  yield api.test(
      'test_suite_with_decription_on_tryserver',
      try_build(
          test_spec={
              'name': 'base_unittests',
              'test': 'gtest_test',
              'description': 'This is a description.'
          }),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.StepTextContains,
                       'base_unittests (with patch)',
                       ['This is a description.']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'test_suite_with_decription_on_ci_builder',
      ci_build(
          test_spec={
              'name': 'base_unittests',
              'test': 'gtest_test',
              'description': 'This is a description.'
          }),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.StepTextContains, 'base_unittests',
                       ['This is a description.']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'spec_error',
      ci_build(
          test_spec={
              'name': 'base_unittests',
              'isolate_name': 'base_unittests_run',
              'results_handler': 'bogus',
          }),
      test_spec_format_error(
          'contains a custom results_handler "bogus"'
          ' but that result handler was not found',
          step_name='isolated_scripts spec format error'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'merge_invalid',
      ci_build(
          test_spec={
              'name': 'base_unittests',
              'isolate_name': 'base_unittests_run',
              'merge': {
                  'script': 'path/to/script.py',
              },
              'swarming': {
                  'can_use_on_swarming_builders': True,
              },
          }),
      test_spec_format_error(
          'contains a custom merge_script "path/to/script.py"'
          " that doesn't match the expected format"),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'precommit_args',
      try_build(
          test_spec={
              'name': 'base_unittests',
              'isolate_name': 'base_unittests_run',
              'args': ['--should-be-in-output',],
              'precommit_args': ['--should-also-be-in-output',],
          }),
  )

  yield api.test(
      'blink_web_tests_with_suffixes',
      ci_build(
          test_spec={
              'name': 'blink_web_tests',
              'isolate_name': 'webkit_tests',
              'results_handler': 'layout tests',
              'swarming': {
                  'can_use_on_swarming_builders': True,
                  'dimension_sets': [{
                      'os': 'Mac',
                      'gpu': '8086:blah',
                  }],
              },
          }),
      api.post_process(post_process.StepSuccess,
                       'archive results for blink_web_tests'),
  )

  yield api.test(
      'custom_webkit_tests_step_name',
      ci_build(
          test_spec={
              'name': 'custom_webkit_tests',
              'isolate_name': 'webkit_tests',
              'results_handler': 'layout tests',
              'swarming': {
                  'can_use_on_swarming_builders': True,
              },
          }),
  )

  yield api.test(
      'swarming_cipd_packages',
      ci_build(
          test_spec={
              'name': 'base_unittests',
              'isolate_name': 'base_unittests_run',
              'trigger_script': {
                  'script': '//path/to/script.py',
              },
              'swarming': {
                  'can_use_on_swarming_builders':
                      True,
                  'cipd_packages': [{
                      'cipd_package': 'cipd/package/name',
                      'location': '../../cipd/package/location',
                      'revision': 'version:1.0',
                  }],
              },
          }),
      api.post_process(
          post_process.StepCommandContains,
          'test_pre_run.[trigger (custom trigger script)] base_unittests', [
              '-cipd-package',
              '../../cipd/package/location:cipd/package/name=version:1.0',
          ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'experimental',
      ci_build(
          test_spec={
              'experiment_percentage': '100',
              'name': 'base_unittests',
              'isolate_name': 'base_unittests_run',
          }),
      api.step_data('base_unittests (experimental)', retcode=1),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'experimental_off',
      ci_build(
          test_spec={
              'experiment_percentage': '0',
              'name': 'base_unittests',
              'isolate_name': 'base_unittests_run',
          }),
      api.post_process(post_process.MustRun,
                       'experimental tests not in experiment'),
      api.post_process(post_process.DoesNotRunRE, '.*base_unittests.*'),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'ci_test_on_ci_builder',
      ci_build(test_spec={
          'name': 'script_test',
          'ci_only': True,
      }),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.MustRun, 'script_test'),
      api.post_process(post_process.StepTextContains, 'script_test',
                       ['This test will not be run on try builders']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'swarmed_ci_test_on_ci_builder',
      ci_build(
          test_spec={
              'name': 'script_test',
              'ci_only': True,
              'swarming': {
                  'can_use_on_swarming_builders': True
              },
          }),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.MustRun, 'script_test'),
      api.post_process(post_process.StepTextContains, 'script_test',
                       ['This test will not be run on try builders']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'ci_test_on_try_builder',
      try_build(test_spec={
          'name': 'script_test',
          'ci_only': True,
      }),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.StepTextContains, 'ci_only tests',
                       ['* script_test']),
      api.post_process(post_process.DoesNotRun, 'script_test (with patch)'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'swarmed_ci_test_on_try_builder',
      try_build(
          test_spec={
              'name': 'script_test',
              'ci_only': True,
              'swarming': {
                  'can_use_on_swarming_builders': True
              },
          }),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.StepTextContains, 'ci_only tests',
                       ['* script_test']),
      api.post_process(post_process.DoesNotRun, 'script_test (with patch)'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'ci_test_on_try_builder_with_bypass',
      try_build(test_spec={
          'name': 'script_test',
          'ci_only': True,
      }),
      api.step_data('parse description',
                    api.json.output({'Include-Ci-Only-Tests': ['true']})),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.MustRun, 'script_test (with patch)'),
      api.post_process(post_process.StepTextContains,
                       'script_test (with patch)',
                       [('This test is being run due to the'
                         ' Include-Ci-Only-Tests gerrit footer')]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'swarmed_ci_test_on_try_builder_with_bypass',
      try_build(
          test_spec={
              'name': 'script_test',
              'ci_only': True,
              'swarming': {
                  'can_use_on_swarming_builders': True
              },
          }),
      api.step_data('parse description',
                    api.json.output({'Include-Ci-Only-Tests': ['true']})),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.MustRun, 'script_test (with patch)'),
      api.post_process(post_process.StepTextContains,
                       'script_test (with patch)',
                       [('This test is being run due to the'
                         ' Include-Ci-Only-Tests gerrit footer')]),
      api.post_process(post_process.DropExpectation),
  )
