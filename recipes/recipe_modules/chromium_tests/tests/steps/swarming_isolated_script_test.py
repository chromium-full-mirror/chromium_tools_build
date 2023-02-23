# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import collections

from recipe_engine import post_process

from RECIPE_MODULES.build.chromium_tests import steps

DEPS = [
    'chromium',
    'chromium_swarming',
    'chromium_tests',
    'chromium_tests_builder_config',
    'depot_tools/bot_update',
    'isolate',
    'profiles',
    'recipe_engine/assertions',
    'recipe_engine/commit_position',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/step',
    'recipe_engine/swarming',
    'test_utils',
]


def RunSteps(api):
  # Create a nested step so that setup steps can be easily filtered out
  with api.step.nest('setup steps'):
    api.chromium.set_build_properties({
        'got_webrtc_revision': 'webrtc_sha',
        'got_v8_revision': 'v8_sha',
    })
    api.chromium.set_config('chromium')
    # Fake path, as the real one depends on having done a chromium checkout.
    api.profiles.src_dir = api.path['start_dir']

    _, builder_config = api.chromium_tests_builder_config.lookup_builder()
    api.chromium_tests.configure_build(builder_config)
    api.chromium_tests.prepare_checkout(builder_config)

    test_repeat_count = api.properties.get('repeat_count')
    if api.properties.get('swarm_hashes'):
      swarm_hashes = api.properties['swarm_hashes']
      assert len(swarm_hashes) == 1
      test_name = list(swarm_hashes)[0]
    else:
      # Needed for a test
      test_name = 'base_unittests'
    isolate_coverage_data = api.properties.get('isolate_coverage_data', False)
    test_spec = steps.SwarmingIsolatedScriptTestSpec.create(
        name=test_name,
        waterfall_builder_group='waterfall_builder_group',
        waterfall_buildername='waterfall_buildername',
        override_compile_targets=api.properties.get('override_compile_targets'),
        io_timeout=120,
        hard_timeout=360,
        expiration=7200,
        shards=1,
        dimensions=api.properties.get('dimensions', {
            'gpu': '8086',
        }),
        isolate_coverage_data=isolate_coverage_data,
        quickrun_shards=api.properties.get('quickrun_shards', 0),
        inverse_quickrun_shards=api.properties.get('inverse_quickrun_shards',
                                                   0))
    override_shards = api.properties.get('shards')
    if override_shards:
      test_spec = test_spec.with_shards(override_shards)
    test = test_spec.get_test(api.chromium_tests)
    if 'inverse_quickrun_shards' in api.properties:
      test.inverted_raw_cmd = ['run', 'inverted.filter']
      test.is_inverted_rts = True
    api.chromium_swarming.set_default_dimension('pool', 'foo')
    assert test.runs_on_swarming
    assert test.shards > 0

    if test_repeat_count:
      test.test_options = steps.TestOptions.create(
          test_filter=api.properties.get('test_filter'),
          repeat_count=test_repeat_count,
          retry_limit=0,
          run_disabled=bool(test_repeat_count))

  try:
    api.test_utils.run_tests_once([test], 'with patch')

  finally:
    if api.properties.get('run_without_patch'):
      test._only_retry_failed_tests = True

      test.pre_run('without patch')
      test.run('without patch')

    result = api.step('details', [])
    result.presentation.logs['details'] = [
        'compile_targets: %r' % test.compile_targets(),
        'uses_local_devices: %r' % test.uses_local_devices,
        'uses_isolate: %r' % test.uses_isolate,
    ]
    if test_name == 'blink_web_tests':
      result.presentation.logs['details'].append(
          'pass_fail_counts: %r' % test.pass_fail_counts(suffix='with patch'))
      result.presentation.logs['details'].append(
          'has_valid_results: %r' % test.has_valid_results('with patch'))

    if 'expected_has_valid_results' in api.properties:
      api.assertions.assertEqual(
          test.has_valid_results('with patch'),
          api.properties['expected_has_valid_results'])


def GenTests(api):
  ctbc_api = api.chromium_tests_builder_config

  def arbitrary_tester():
    return sum([
        api.platform('linux', 64),
        api.chromium.ci_build(
            builder_group='fake-group',
            builder='fake-tester',
            parent_buildername='fake-builder'),
        ctbc_api.properties(
            ctbc_api.properties_assembler_for_ci_tester(
                builder_group='fake-group',
                builder='fake-tester',
            ).with_parent(
                builder_group='fake-group',
                builder='fake-builder',
            ).assemble()),
    ], api.empty_test_data())

  def filter_out_setup_steps():

    def step_filter(check, step_odict):
      del check
      return collections.OrderedDict([(k, v)
                                      for k, v in step_odict.items()
                                      if not k.startswith('setup steps')])

    return api.post_process(step_filter)

  yield api.test(
      'basic',
      arbitrary_tester(),
      api.properties(swarm_hashes={
          'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/111',
      }),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'missing_shards',
      arbitrary_tester(),
      api.properties(
          swarm_hashes={
              'blink_web_tests':
                  'ffffffffffffffffffffffffffffffffffffffff/size',
          },
          expected_has_valid_results=False,
      ),
      api.override_step_data(
          'blink_web_tests on Intel GPU on Linux (with patch)',
          api.chromium_swarming.canned_summary_output(
              api.test_utils.m.json.output({
                  'missing_shards': [0],
              }, 0),
              shards=1)),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'isolate_coverage_data',
      arbitrary_tester(),
      api.properties(
          swarm_hashes={
              'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
          },
          isolate_coverage_data=True,
      ),
      api.post_check(
          api.swarming.check_triggered_request,
          'test_pre_run (with patch).[trigger] base_unittests on Intel GPU on '
          'Linux (with patch)',
          lambda check, req: check('LLVM_PROFILE_FILE' in req[0].env_vars),
      ),
      api.post_process(
          post_process.StepCommandContains,
          'base_unittests on Intel GPU on Linux (with patch)',
          ['[START_DIR]/testing/merge_scripts/code_coverage/merge_results.py'],
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'fail',
      arbitrary_tester(),
      api.properties(
          swarm_hashes={
              'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
          },),
      api.step_data(
          'test_pre_run (with patch).[trigger] base_unittests on Intel GPU on '
          'Linux (with patch)',
          retcode=1),
      api.post_process(post_process.StatusAnyFailure),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'fail_many_failures',
      arbitrary_tester(),
      api.properties(
          swarm_hashes={
              'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
          },
          shards=20,
          run_without_patch=True,
      ),
      api.step_data(
          'test_pre_run (with patch).[trigger] base_unittests on Intel GPU on '
          'Linux (with patch)',
          retcode=1,
      ),
      api.post_process(post_process.StatusAnyFailure),
      api.post_process(
          post_process.Filter(
              '[trigger] base_unittests on Intel GPU on Linux (without patch)')
      ),
  )

  yield api.test(
      'fail_to_trigger',
      arbitrary_tester(),
      api.post_process(post_process.StatusException),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'without_patch_filter',
      arbitrary_tester(),
      api.properties(
          swarm_hashes={
              'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
          },
          run_without_patch='a'),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'base_unittests',
          'with patch',
          extra_suffix='on Intel GPU on Linux',
          failures=['Test.Two']),
      api.post_process(
          post_process.Filter(
              '[trigger] base_unittests on Intel GPU on Linux (without patch)')
      ),
  )

  yield api.test(
      'expected_failures',
      arbitrary_tester(),
      api.properties(
          swarm_hashes={
              'blink_web_tests':
                  'ffffffffffffffffffffffffffffffffffffffff/size',
          },
          test_filter=['test1', 'test2'],
          repeat_count=20,
      ),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'blink_web_tests',
          'with patch',
          extra_suffix='on Intel GPU on Linux',
          failures=['Test1', 'Test2', 'Test3', 'Test4']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'customized_test_options',
      arbitrary_tester(),
      api.properties(
          swarm_hashes={
              'blink_web_tests':
                  'ffffffffffffffffffffffffffffffffffffffff/size',
          },
          test_filter=['test1', 'test2'],
          repeat_count=20,
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'override_compile_targets',
      arbitrary_tester(),
      api.properties(
          swarm_hashes={
              'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
          },
          override_compile_targets=['base_unittests_run']),
      filter_out_setup_steps(),
  )

  yield api.test(
      'chartjson',
      arbitrary_tester(),
      api.properties(
          swarm_hashes={
              'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
          },
          perf_builder_name_alias='test-perf-id',
          results_url='https://example/url'),
      filter_out_setup_steps(),
  )

  yield api.test(
      'chartjson_max_failures',
      arbitrary_tester(),
      api.properties(
          swarm_hashes={
              'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
          },
          perf_builder_name_alias='test-perf-id',
          results_url='https://example/url'),
      api.override_step_data(
          'base_unittests on Intel GPU on Linux (with patch)',
          api.chromium_swarming.canned_summary_output(
              api.json.output({}), shards=2),
          retcode=102),
      filter_out_setup_steps(),
  )

  yield api.test(
      'chartjson_no_results_failure',
      arbitrary_tester(),
      api.properties(
          swarm_hashes={
              'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
          },
          perf_builder_name_alias='test-perf-id',
          results_url='https://example/url'),
      api.override_step_data(
          'base_unittests on Intel GPU on Linux (with patch)',
          api.chromium_swarming.canned_summary_output(
              api.json.output({}), failure=True)),
      filter_out_setup_steps(),
  )

  yield api.test(
      'histograms_LUCI_missing_perf_dashboard_machine_group_property',
      arbitrary_tester(),
      api.properties(
          swarm_hashes={
              'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
          },
          got_webrtc_revision='ffffffffffffffffffffffffffffffffffffffff',
          got_v8_revision='ffffffffffffffffffffffffffffffffffffffff',
          perf_builder_name_alias='test-perf-id',
          results_url='https://example/url'),
      filter_out_setup_steps(),
  )

  yield api.test(
      'dimensions_windows',
      arbitrary_tester(),
      api.properties(
          swarm_hashes={
              'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
          },
          dimensions={
              'gpu': '8086',
              'os': 'Windows',
          }),
      filter_out_setup_steps(),
  )

  yield api.test(
      'dimensions_mac',
      arbitrary_tester(),
      api.properties(
          swarm_hashes={
              'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
          },
          dimensions={
              'gpu': '8086',
              'os': 'Mac',
          }),
      filter_out_setup_steps(),
  )

  yield api.test(
      'dimensions_mac_hidpi',
      arbitrary_tester(),
      api.properties(
          swarm_hashes={
              'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
          },
          dimensions={
              'gpu': '8086',
              'os': 'Mac',
              'hidpi': '1',
          }),
      filter_out_setup_steps(),
  )

  yield api.test(
      'dimensions_android',
      arbitrary_tester(),
      api.properties(
          swarm_hashes={
              'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
          },
          dimensions={
              'device_type': 'bullhead',
              'device_os': 'LOL123',
              'os': 'Android',
          }),
      filter_out_setup_steps(),
  )

  yield api.test(
      'invalid_test_results',
      arbitrary_tester(),
      api.properties(
          swarm_hashes={
              'blink_web_tests':
                  'ffffffffffffffffffffffffffffffffffffffff/size',
          },
          test_filter=['test1', 'test2'],
          repeat_count=20,
      ),
      api.override_step_data(
          'blink_web_tests on Intel GPU on Linux (with patch)',
          api.chromium_swarming.canned_summary_output(
              api.test_utils.m.json.output(None, 255), shards=2)),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'shard_timed_out_failure',
      arbitrary_tester(),
      api.properties(swarm_hashes={
          'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
      }),
      api.override_step_data(
          'base_unittests on Intel GPU on Linux (with patch)',
          api.chromium_swarming.summary(
              dispatched_task_step_test_data=None,
              data={
                  'shards': [{
                      'created_ts': '2014-09-25T01:41:00.123',
                      'started_ts': '2014-09-25T01:42:11.123',
                      'completed_ts': '2014-09-25T01:43:11.123',
                      'duration': 31.5,
                      'state': 'TIMED_OUT'
                  }]
              })),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'quickrun_shards',
      arbitrary_tester(),
      api.properties(
          **{
              "$recipe_engine/cq": {
                  "active": True,
                  "runMode": "QUICK_DRY_RUN",
                  "topLevel": True
              }
          }),
      api.properties(
          swarm_hashes={
              'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/111',
          },
          run_without_patch=True,
          quickrun_shards=2),
      api.step_data(
          'test_pre_run (with patch).[trigger] base_unittests on Intel GPU on '
          'Linux (with patch)',
          retcode=1,
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'inverse_quickrun_shards',
      arbitrary_tester(),
      api.properties(
          swarm_hashes={
              'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/111',
          },
          run_without_patch=True,
          inverse_quickrun_shards=2),
      api.step_data(
          'test_pre_run (with patch).[trigger] base_unittests on Intel GPU on '
          'Linux (with patch)',
          retcode=1,
      ),
      api.post_process(post_process.DropExpectation),
  )
