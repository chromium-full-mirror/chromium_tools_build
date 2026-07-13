# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

DEPS = [
    'chromium_swarming',
    'chromium_tests',
    'chromium_tests_builder_config',
    'recipe_engine/properties',
    'recipe_engine/swarming',
]

def RunSteps(api):
  api.chromium_swarming.set_default_dimension('os', 'Linux')
  builder_id, builder_config = (
      api.chromium_tests_builder_config.lookup_builder())
  build_result, _ = api.chromium_tests.main_waterfall_steps(
      builder_id, builder_config)
  return build_result


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

  def common_test_data(test_spec):
    return api.chromium_tests.read_targets_spec('test-group', {
        'test-builder': {
            'gtest_tests': [test_spec],
        },
    })

  def ci_build(test_spec, **kwargs):
    t = api.chromium_tests_builder_config.ci_build(
        builder_group='test-group',
        builder='test-builder',
        builder_db=builder_db,
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
                  'dimensions': {
                      'os': 'Linux',
                      'foo': None,
                  },
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
      'swarming_with_legacy_optional_dimensions',
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
                  'dimensions': {
                      'os': 'Linux',
                  },
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
                  'dimensions': {
                      'os': 'Linux',
                  },
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
      'swarming_with_server',
      ci_build(
          test_spec={
              'test': 'base_unittests',
              'swarming': {
                  'dimensions': {
                      'os': 'Linux',
                  },
                  'server': 'other-swarming.appspot.com',
              },
          }),
      api.post_process(post_process.StepCommandContains,
                       'test_pre_run.[trigger] base_unittests', [
                           '-server',
                           'other-swarming.appspot.com',
                       ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'swarming_with_realm',
      ci_build(
          test_spec={
              'test': 'base_unittests',
              'swarming': {
                  'dimensions': {
                      'os': 'Linux',
                  },
                  'realm': 'project:customrealm'
              },
          }),
      api.post_check(
          api.swarming.check_triggered_request,
          'test_pre_run.[trigger] base_unittests',
          lambda check, req: check(req.realm == 'project:customrealm'),
      ),
      api.post_process(post_process.DropExpectation),
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
      'swarming_wait_for_capacity',
      ci_build(
          test_spec={
              'test': 'base_unittests',
              'swarming': {
                  'dimensions': {
                      'os': 'Linux',
                  },
                  'wait_for_capacity': True,
              },
          }),
      api.post_check(
          api.swarming.check_triggered_request,
          'test_pre_run.[trigger] base_unittests',
          lambda check, req: check(req[0].wait_for_capacity),
      ),
      api.post_process(post_process.DropExpectation),
  )
