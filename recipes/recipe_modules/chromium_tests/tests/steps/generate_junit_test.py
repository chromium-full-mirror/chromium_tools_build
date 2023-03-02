# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

DEPS = [
    'chromium_tests',
    'chromium_tests_builder_config',
    'filter',
    'test_utils',
    'depot_tools/tryserver',
    'recipe_engine/json',
    'recipe_engine/raw_io',
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
                  chromium_config_kwargs={
                      'TARGET_PLATFORM': 'android',
                  },
                  android_config='x86_builder',
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
            'junit_tests': [test_spec],
        },
    })

    test_name = test_spec.get('name') or test_spec['test']

    step_filter = post_process.Filter()
    # Any step with the test name in it
    step_filter = step_filter.include_re(
        r'.*\b{}\b'.format(test_name), at_least=0)
    # The step for reporting ci_only tests
    step_filter = step_filter.include_re('ci_only tests$', at_least=0)
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

  yield api.test(
      'basic',
      ci_build(test_spec={
          'test': 'junit_test',
      }),
  )

  yield api.test(
      'different-name',
      ci_build(test_spec={
          'test': 'junit_test',
          'name': 'junit_alias',
      }),
      api.post_process(post_process.MustRun, 'junit_alias'),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'additional-args',
      ci_build(test_spec={
          'test': 'junit_test',
          'args': ['--foo=bar'],
      }),
      api.post_process(post_process.MustRun, 'junit_test'),
      api.post_process(post_process.StepCommandContains, 'junit_test',
                       ['--foo=bar']),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'junit-with-rdb-by-default',
      ci_build(test_spec={
          'foo': 'bar',
          'test': 'junit_test',
          'args': ['--foo=bar'],
      }),
      api.post_process(post_process.MustRun, 'junit_test'),
      api.post_process(post_process.StepCommandContains, 'junit_test', [
          'rdb', 'stream', '-var', 'builder:test-builder', '-var',
          'test_suite:junit_test', '-tag', 'step_name:junit_test', '-tag',
          'target_platform:android', '-coerce-negative-duration', '-new',
          '-realm', 'chromium:ci', '-include', '-exonerate-unexpected-pass',
          '--'
      ]),
      api.post_process(post_process.StepCommandContains, 'junit_test',
                       ['--foo=bar']),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'junit-with-test-spec-rdb-override',
      ci_build(
          test_spec={
              'foo': 'bar',
              'test': 'junit_test',
              'args': ['--foo=bar'],
              'resultdb': {
                  'enable': True,
                  'test_id_prefix': 'prefix'
              },
          },),
      api.post_process(post_process.MustRun, 'junit_test'),
      api.post_process(post_process.StepCommandContains, 'junit_test', [
          'rdb', 'stream', '-test-id-prefix', 'prefix', '-var',
          'builder:test-builder', '-var', 'test_suite:junit_test', '-tag',
          'step_name:junit_test', '-tag', 'target_platform:android',
          '-coerce-negative-duration', '-new', '-realm', 'chromium:ci',
          '-include', '-exonerate-unexpected-pass', '--'
      ]),
      api.post_process(post_process.StepCommandContains, 'junit_test',
                       ['--foo=bar']),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'test_suite_with_decription_on_tryserver',
      try_build(test_spec={
          'test': 'gtest_test',
          'description': 'This is a description.'
      }),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.StepTextContains, 'gtest_test (with patch)',
                       ['This is a description.']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'test_suite_with_decription_on_ci_builder',
      ci_build(test_spec={
          'test': 'gtest_test',
          'description': 'This is a description.'
      }),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.StepTextContains, 'gtest_test',
                       ['This is a description.']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'ci_only_test_on_tryserver',
      try_build(test_spec={
          'ci_only': True,
          'test': 'junit_test',
      },),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.StepTextContains, 'ci_only tests',
                       ['* junit_test']),
      api.post_process(post_process.DoesNotRun, 'junit_test (with patch)'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'ci_only_test_on_tryserver_with_bypass',
      try_build(test_spec={
          'ci_only': True,
          'test': 'junit_test',
      },),
      api.step_data('parse description',
                    api.json.output({'Include-Ci-Only-Tests': ['true']})),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.MustRun, 'junit_test (with patch)'),
      api.post_process(post_process.StepTextContains, 'junit_test (with patch)',
                       [('This test is being run due to the'
                         ' Include-Ci-Only-Tests gerrit footer')]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'ci_only_test_on_ci_builder',
      ci_build(test_spec={
          'ci_only': True,
          'test': 'junit_test',
      },),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.MustRun, 'junit_test'),
      api.post_process(post_process.StepTextContains, 'junit_test',
                       ['This test will not be run on try builders']),
      api.post_process(post_process.DropExpectation),
  )
