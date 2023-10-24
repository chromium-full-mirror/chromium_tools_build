# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

DEPS = [
    'chromium_swarming',
    'chromium_tests',
    'chromium_tests_builder_config',
    'filter',
    'depot_tools/tryserver',
    'recipe_engine/json',
    'recipe_engine/properties',
    'recipe_engine/swarming',
]

def RunSteps(api):
  api.chromium_swarming.set_default_dimension('os', 'Linux')
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
            'gtest_tests': [test_spec],
        },
    })

    test_name = test_spec['test']

    step_filter = post_process.Filter()
    # Any step with the test name in it
    step_filter = step_filter.include_re(
        r'.*\b{}\b'.format(test_name), at_least=0)
    # Any errors resulting from generating the test
    step_filter = step_filter.include_re(r'.*\berror$', at_least=0)
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

  def test_spec_format_error(s):
    """Adds a post check for step 'test spec format error'.

    The prose contained in the details log must contain the substring
    `s`.
    """

    def check(check, steps):
      details = steps['test spec format error'].logs['details']
      details = details.replace('\n', ' ')
      check(s in details)

    return api.post_check(check)

  yield api.test(
      'basic',
      ci_build(test_spec={
          'test': 'base_unittests',
          'total_shards': 2,
      },),
      api.post_process(post_process.StepCommandContains, 'base_unittests', [
          'base_unittests',
          '--test-launcher-shard-index=0',
          '--test-launcher-total-shards=2',
      ]),
      api.post_process(post_process.DropExpectation),
  )

  logdog_path = '{$HOME}/logdog'
  logdog_package = 'infra/logdog/linux-386'
  logdog_revision = 'git_revision:deadbeef'

  yield api.test(
      'swarming',
      ci_build(
          test_spec={
              'test': 'base_unittests',
              'test_target': '//base:base_unittests',
              'swarming': {
                  'dimension_sets': [{
                      'os': 'Linux',
                      'foo': None,
                  }],
                  'optional_dimensions': {
                      '60': {
                          'bar': 'baz',
                      },
                  },
                  'cipd_packages': [{
                      'location': logdog_path,
                      'cipd_package': logdog_package,
                      'revision': logdog_revision,
                  }],
              },
          }),
      api.post_process(
          api.swarming.check_triggered_request,
          'test_pre_run.[trigger] base_unittests',
          lambda check, req: check(
              all(('os', 'Linux') in slice.dimensions.items()
                  for slice in req)),
          lambda check, req: check(not any('foo' in slice.dimensions
                                           for slice in req)),
          lambda check, req: check(('bar', 'baz') in req[0].dimensions.items()),
          lambda check, req: check(not any('bar' in slice.dimensions
                                           for slice in req[1:])),
          lambda check, req: check(logdog_path in req[0].cipd_ensure_file.
                                   packages),
          lambda check, req: check(logdog_package == req[0].cipd_ensure_file.
                                   packages[logdog_path][0].name),
          lambda check, req: check(logdog_revision == req[0].cipd_ensure_file.
                                   packages[logdog_path][0].version),
      ),
      api.post_process(post_process.StepCommandContains, 'base_unittests', [
          'swarming',
          'collect',
      ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'swarming_dimensions',
      ci_build(
          test_spec={
              'test': 'base_unittests',
              'test_target': '//base:base_unittests',
              'swarming': {
                  'dimensions': {
                      'os': 'Linux',
                      'foo': None,
                  },
                  'optional_dimensions': {
                      '60': {
                          'bar': 'baz',
                      },
                  },
              },
          }),
      api.post_process(
          api.swarming.check_triggered_request,
          'test_pre_run.[trigger] base_unittests',
          lambda check, req: check(
              all(('os', 'Linux') in slice.dimensions.items()
                  for slice in req)),
          lambda check, req: check(not any('foo' in slice.dimensions
                                           for slice in req)),
          lambda check, req: check(('bar', 'baz') in req[0].dimensions.items()),
          lambda check, req: check(not any('bar' in slice.dimensions
                                           for slice in req[1:])),
      ),
      api.post_process(post_process.StepCommandContains, 'base_unittests', [
          'swarming',
          'collect',
      ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'swarming_with_allowed_failure_percentage',
      ci_build(
          test_spec={
              'test': 'base_unittests',
              'test_target': '//base:base_unittests',
              'allowed_failure_percentage': 30,
              'swarming': {
                  'can_use_on_swarming_builders': True,
                  'dimension_sets': [{
                      'os': 'Linux',
                      'foo': None,
                  }],
              },
          }),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests',
          '',
          failures=['Test1'],
          successes=['Test2', 'Test3', 'Test4'],
      ),
      api.post_process(post_process.StepTextContains,
                       'Skip the failure of base_unittests', [
                           'Allowed failure percentage: 30%',
                           'Deterministic failures: 1', 'Total: 4'
                       ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'swarming_with_allowed_failure_percentage_no_banner_if_all_passed',
      ci_build(
          test_spec={
              'test': 'base_unittests',
              'test_target': '//base:base_unittests',
              'allowed_failure_percentage': 30,
              'swarming': {
                  'can_use_on_swarming_builders': True,
                  'dimension_sets': [{
                      'os': 'Linux',
                      'foo': None,
                  }],
              },
          }),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests', '', successes=['Test1']),
      api.post_process(post_process.DoesNotRun,
                       'Skip the failure of base_unittests'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'swarming_with_release_blocker_configured',
      ci_build(
          test_spec={
              'test': 'base_unittests',
              'test_target': '//base:base_unittests',
              'release_blocker': {
                  'bug_component': 'foo>hoo',
              },
              'swarming': {
                  'can_use_on_swarming_builders': True,
                  'dimension_sets': [{
                      'os': 'Linux',
                      'foo': None,
                  }],
              },
          }),
      api.override_step_data(
          'base_unittests',
          api.chromium_swarming.canned_summary_output(
              None, failure=True, retcode=1),
      ),
      api.post_process(post_process.StepTextContains, 'base_unittests',
                       ['Release Blocker Failure', 'Owner: foo>hoo']),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'swarming_with_legacy_optional_dimensions',
      ci_build(
          test_spec={
              'test': 'base_unittests',
              'test_target': '//base:base_unittests',
              'swarming': {
                  'dimension_sets': [{
                      'os': 'Linux',
                      'foo': None,
                  },],
                  'optional_dimensions': {
                      '60': [{
                          'bar': 'baz',
                      }],
                  },
              },
          }),
      api.post_process(
          api.swarming.check_triggered_request,
          'test_pre_run.[trigger] base_unittests',
          lambda check, req: check(('bar', 'baz') in req[0].dimensions.items()),
      ),
      api.post_process(post_process.StepCommandContains, 'base_unittests', [
          'swarming',
          'collect',
      ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'use_isolated_scripts_api_in_gtest',
      ci_build(test_spec={
          'test': 'base_unittests',
          'use_isolated_scripts_api': True,
      }),
      api.properties(swarm_hashes={
          'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff',
      }),
      api.post_process(post_process.StepCommandContains, 'base_unittests',
                       ['--isolated-script-test-output']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'do_not_use_isolated_scripts_api_in_gtest',
      ci_build(test_spec={
          'test': 'base_unittests',
          'use_isolated_scripts_api': False,
      }),
      api.post_process(post_process.StepCommandContains, 'base_unittests',
                       ['--test-launcher-summary-output']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'service_account',
      ci_build(
          test_spec={
              'test': 'base_unittests',
              'swarming': {
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
      'swarming_plus_optional_dimension',
      ci_build(
          test_spec={
              'test': 'base_unittests',
              'swarming': {
                  'dimension_sets': [{
                      'os': 'Linux',
                  }],
                  'cipd_packages': [{
                      'location': '{$HOME}/logdog',
                      'cipd_package': 'infra/logdog/linux-386',
                      'revision': 'git_revision:deadbeef',
                  }],
              },
          }),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'swarming_with_named_caches',
      ci_build(
          test_spec={
              'test': 'base_unittests',
              'swarming': {
                  'dimension_sets': [{
                      'os': 'Linux',
                  },],
                  'named_caches': [{
                      'name': 'cache_name',
                      'path': '.path/to/named/cache',
                  },]
              },
          }),
      api.post_check(
          api.swarming.check_triggered_request,
          'test_pre_run.[trigger] base_unittests',
          lambda check, req: check(req[0].named_caches['cache_name'] ==
                                   '.path/to/named/cache'),
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'swarming_with_dimensions_and_dimension_sets',
      ci_build(
          test_spec={
              'test': 'base_unittests',
              'swarming': {
                  'dimension_sets': [{
                      'os': 'Linux',
                  },],
                  'dimensions': {
                      'os': 'Linux',
                  },
                  'named_caches': [{
                      'name': 'cache_name',
                      'path': '.path/to/named/cache',
                  },]
              },
          }),
      test_spec_format_error(
          'specifies both "dimension_sets" and "dimensions"'),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'merge',
      ci_build(
          test_spec={
              'test': 'base_unittests',
              'merge': {
                  'script': '//merge_script.py',
              },
              'swarming': {},
          }),
      api.post_process(post_process.StepCommandContains, 'base_unittests', [
          '--merge-script',
          '[CACHE]/builder/src/merge_script.py',
          '--merge-script-stdout-file',
          '/path/to/tmp/merge_script_log',
      ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'merge_invalid',
      ci_build(
          test_spec={
              'test': 'base_unittests',
              'merge': {
                  'script': 'merge_script.py',
              },
              'swarming': {},
          }),
      test_spec_format_error('contains a custom merge_script "merge_script.py"'
                             " that doesn't match the expected format"),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'trigger_script',
      ci_build(
          test_spec={
              'test': 'base_unittests',
              'trigger_script': {
                  'script': '//trigger_script.py',
              },
              'swarming': {},
          }),
      api.post_process(
          post_process.StepCommandContains,
          'test_pre_run.[trigger (custom trigger script)] base_unittests', [
              'vpython3',
              '[CACHE]/builder/src/trigger_script.py',
              'trigger',
          ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'trigger_script_simultaneous_shard_dispatch',
      ci_build(
          test_spec={
              'test': 'base_unittests',
              'trigger_script': {
                  'script': '//perf_device_trigger.py',
                  'requires_simultaneous_shard_dispatch': True,
              },
              'swarming': {
                  'shards': 5,
              },
          }),
      api.post_process(
          post_process.StepCommandContains,
          'test_pre_run.[trigger (custom trigger script)] base_unittests', [
              '--shards',
              '5',
          ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'trigger_script_invalid',
      ci_build(
          test_spec={
              'test': 'base_unittests',
              'trigger_script': {
                  'script': 'trigger_script.py',
              },
              'swarming': {},
          }),
      test_spec_format_error(
          'contains a custom trigger_script "trigger_script.py"'
          " that doesn't match the expected format"),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'experimental',
      ci_build(test_spec={
          'experiment_percentage': '100',
          'swarming': {},
          'test': 'base_unittests',
      }),
      api.override_step_data(
          'base_unittests (experimental)',
          api.chromium_swarming.canned_summary_output(None, retcode=1)),
      api.post_process(post_process.DropExpectation),
  )

  def NotIdempotent(check, step_odict, step):
    check('Idempotent flag unexpected',
          '--idempotent' not in step_odict[step].cmd)

  yield api.test(
      'not_idempotent',
      ci_build(test_spec={
          'swarming': {
              'idempotent': False,
          },
          'test': 'base_unittests',
      }),
      api.post_process(NotIdempotent, 'test_pre_run.[trigger] base_unittests'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'test_suite_with_decription_on_tryserver',
      try_build(test_spec={
          'test': 'gtest_test',
          'description': 'This is a description.'
      }),
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
      api.post_process(post_process.StepTextContains, 'gtest_test',
                       ['This is a description.']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'ci_only_test_on_tryserver',
      try_build(test_spec={
          'ci_only': True,
          'test': 'gtest_test',
      }),
      api.post_process(post_process.StepCommandEmpty,
                       'gtest_test (with patch)'),
      api.post_process(
          post_process.StepTextContains,
          'gtest_test (with patch)',
          ["This test is not being run because it is marked 'ci_only'"],
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'swarmed_ci_only_test_on_tryserver',
      try_build(test_spec={
          'ci_only': True,
          'test': 'gtest_test',
          'swarming': {},
      }),
      api.post_process(post_process.StepCommandEmpty,
                       'gtest_test (with patch)'),
      api.post_process(
          post_process.StepTextContains,
          'gtest_test (with patch)',
          ["This test is not being run because it is marked 'ci_only'"],
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'swarmed_ci_only_test_on_tryserver_with_bypass',
      try_build(test_spec={
          'ci_only': True,
          'test': 'gtest_test',
          'swarming': {},
      }),
      api.step_data('parse description',
                    api.json.output({'Include-Ci-Only-Tests': ['true']})),
      api.post_process(post_process.MustRun, 'gtest_test (with patch)'),
      api.post_process(post_process.StepTextContains, 'gtest_test (with patch)',
                       [('This test is being run due to the'
                         ' Include-Ci-Only-Tests gerrit footer')]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'ci_only_test_on_tryserver_with_bypass',
      try_build(test_spec={
          'ci_only': True,
          'test': 'gtest_test',
      }),
      api.step_data('parse description',
                    api.json.output({'Include-Ci-Only-Tests': ['true']})),
      api.post_process(post_process.MustRun, 'gtest_test (with patch)'),
      api.post_process(post_process.StepTextContains, 'gtest_test (with patch)',
                       [('This test is being run due to the'
                         ' Include-Ci-Only-Tests gerrit footer')]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'ci_only_test_on_ci_builder',
      ci_build(test_spec={
          'ci_only': True,
          'test': 'gtest_test',
      }),
      api.post_process(post_process.MustRun, 'gtest_test'),
      api.post_process(post_process.StepTextContains, 'gtest_test',
                       ['This test will not be run on try builders']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'swarmed_ci_only_test_on_ci_builder',
      ci_build(test_spec={
          'ci_only': True,
          'test': 'gtest_test',
          'swarming': {},
      }),
      api.post_process(post_process.MustRun, 'gtest_test'),
      api.post_process(post_process.StepTextContains, 'gtest_test',
                       ['This test will not be run on try builders']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'experimental_test_experiment_off',
      ci_build(test_spec={
          'test': 'gtest_test',
          'experiment_percentage': '0',
      }),
      api.post_process(post_process.StepCommandEmpty,
                       'gtest_test (experimental)'),
      api.post_process(
          post_process.StepTextContains,
          'gtest_test (experimental)',
          ['This test was not selected for its experiment in this build'],
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'experimental_test_experiment_on',
      ci_build(test_spec={
          'test': 'gtest_test',
          'experiment_percentage': '100',
      }),
      api.override_step_data('gtest_test (experimental)', retcode=1),
      api.post_process(post_process.DropExpectation),
  )
