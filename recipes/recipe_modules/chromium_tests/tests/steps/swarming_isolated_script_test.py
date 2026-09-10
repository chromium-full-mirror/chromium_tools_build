# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import collections

from recipe_engine import post_process

from RECIPE_MODULES.build.chromium_tests import steps

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
  chromium,
  chromium_swarming,
  chromium_tests,
  chromium_tests_builder_config,
  isolate,
  profiles,
  test_utils,
)
from RECIPE_MODULES.depot_tools import bot_update
from RECIPE_MODULES.recipe_engine import (
  assertions,
  commit_position,
  json,
  path,
  platform,
  properties,
  raw_io,
  step,
  swarming,
)


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  bot_update: bot_update.API
  chromium: chromium.API
  chromium_swarming: chromium_swarming.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  commit_position: commit_position.API
  isolate: isolate.API
  json: json.API
  path: path.API
  platform: platform.API
  profiles: profiles.API
  properties: properties.API
  raw_io: raw_io.API
  step: step.API
  swarming: swarming.API
  test_utils: test_utils.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API
  chromium_swarming: chromium_swarming.TEST_API
  chromium_tests: chromium_tests.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  platform: platform.TEST_API
  properties: properties.TEST_API
  swarming: swarming.TEST_API
  test_utils: test_utils.TEST_API


def RunSteps(api: DEPS):
  # Create a nested step so that setup steps can be easily filtered out
  with api.step.nest('setup steps'):
    api.chromium.set_build_properties(
      {
        'got_webrtc_revision': 'webrtc_sha',
        'got_v8_revision': 'v8_sha',
      }
    )
    api.chromium.set_config('chromium')
    # Fake path, as the real one depends on having done a chromium checkout.
    api.profiles.src_dir = api.path.start_dir

    _, builder_config = api.chromium_tests_builder_config.lookup_builder()
    api.chromium_tests.configure_build(builder_config)
    (
      update_result,
      _,
      _,
    ) = api.chromium_tests.prepare_checkout(builder_config)

    test_repeat_count = api.properties.get('repeat_count')
    if api.properties.get('swarm_hashes'):
      swarm_hashes = api.properties['swarm_hashes']
      assert len(swarm_hashes) == 1
      test_name = list(swarm_hashes)[0]
    else:
      # Needed for a test
      test_name = 'base_unittests'
    isolate_profile_data = api.properties.get('isolate_profile_data', False)
    test_spec = steps.SwarmingIsolatedScriptTestSpec.create(
      name=test_name,
      waterfall_builder_group='waterfall_builder_group',
      waterfall_buildername='waterfall_buildername',
      io_timeout=120,
      hard_timeout=360,
      expiration=7200,
      shards=1,
      dimensions=api.properties.get(
        'dimensions',
        {
          'gpu': api.properties.get('test_gpu_dimension', '8086'),
          'os': 'Linux',
        },
      ),
      isolate_profile_data=isolate_profile_data,
    )
    override_shards = api.properties.get('shards')
    if override_shards:
      test_spec = test_spec.with_shards(override_shards)
    test = test_spec.get_test(api.chromium_tests)
    api.chromium_swarming.set_default_dimension('pool', 'foo')
    assert test.runs_on_swarming
    assert test.shards > 0

    if test_repeat_count:
      test.test_options = steps.TestOptions.create(
        test_filter=api.properties.get('test_filter'),
        repeat_count=test_repeat_count,
        retry_limit=0,
        run_disabled=bool(test_repeat_count),
      )

  checkout_dir = update_result.checkout_dir
  source_dir = update_result.source_root.path
  build_dir = source_dir / 'out' / 'some_build_dir'
  try:
    api.test_utils.run_tests_once(
      checkout_dir, source_dir, build_dir, [test], 'with patch'
    )

  finally:
    if api.properties.get('run_without_patch'):
      test._only_retry_failed_tests = True

      test.pre_run('without patch')
      test.run(checkout_dir, source_dir, build_dir, 'without patch')

    result = api.step('details', [])
    result.presentation.logs['details'] = [
      'compile_targets: %r' % test.compile_targets(),
      'uses_local_devices: %r' % test.uses_local_devices,
      'uses_isolate: %r' % test.uses_isolate,
    ]
    if test_name == 'blink_web_tests':
      result.presentation.logs['details'].append(
        'has_valid_results: %r' % test.has_valid_results('with patch')
      )


def GenTests(api: TEST_DEPS):
  ctbc_api = api.chromium_tests_builder_config

  def arbitrary_tester():
    return sum(
      [
        api.platform('linux', 64),
        api.chromium.ci_build(
          builder_group='fake-group',
          builder='fake-tester',
          parent_buildername='fake-builder',
        ),
        ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_tester(
            builder_group='fake-group',
            builder='fake-tester',
          )
          .with_parent(
            builder_group='fake-group',
            builder='fake-builder',
          )
          .assemble()
        ),
      ],
      api.empty_test_data(),
    )

  yield api.test(
    'basic',
    arbitrary_tester(),
    api.properties(
      swarm_hashes={
        'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/111',
      }
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'isolate_profile_data',
    arbitrary_tester(),
    api.properties(
      swarm_hashes={
        'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
      },
      isolate_profile_data=True,
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
      [
        (
          '[CACHE]/builder/src/testing/merge_scripts/code_coverage/'
          'merge_results.py'
        )
      ],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'isolate_profile_data_gpu_model_specified',
    arbitrary_tester(),
    api.properties(
      swarm_hashes={
        'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
      },
      isolate_profile_data=True,
      test_gpu_dimension='8086:1234-5678',
    ),
    api.post_check(
      api.swarming.check_triggered_request,
      'test_pre_run (with patch).[trigger] base_unittests on Intel 0x1234 '
      'GPU on Linux (with patch)',
      lambda check, req: check('LLVM_PROFILE_FILE' in req[0].env_vars),
    ),
    api.post_process(
      post_process.StepCommandContains,
      'base_unittests on Intel 0x1234 GPU on Linux (with patch)',
      [
        (
          '[CACHE]/builder/src/testing/merge_scripts/code_coverage/'
          'merge_results.py'
        )
      ],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'isolate_profile_data_unknown_gpu',
    arbitrary_tester(),
    api.properties(
      swarm_hashes={
        'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
      },
      isolate_profile_data=True,
      test_gpu_dimension='9999',
    ),
    api.post_check(
      api.swarming.check_triggered_request,
      'test_pre_run (with patch).[trigger] base_unittests on (9999) GPU on '
      'Linux (with patch)',
      lambda check, req: check('LLVM_PROFILE_FILE' in req[0].env_vars),
    ),
    api.post_process(
      post_process.StepCommandContains,
      'base_unittests on (9999) GPU on Linux (with patch)',
      [
        (
          '[CACHE]/builder/src/testing/merge_scripts/code_coverage/'
          'merge_results.py'
        )
      ],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'isolate_profile_data_unknown_gpu_with_model',
    arbitrary_tester(),
    api.properties(
      swarm_hashes={
        'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
      },
      isolate_profile_data=True,
      test_gpu_dimension='9999:1234-5678',
    ),
    api.post_check(
      api.swarming.check_triggered_request,
      'test_pre_run (with patch).[trigger] base_unittests on '
      '(9999:1234) GPU on Linux (with patch)',
      lambda check, req: check('LLVM_PROFILE_FILE' in req[0].env_vars),
    ),
    api.post_process(
      post_process.StepCommandContains,
      'base_unittests on (9999:1234) GPU on Linux (with patch)',
      [
        (
          '[CACHE]/builder/src/testing/merge_scripts/code_coverage/'
          'merge_results.py'
        )
      ],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'isolate_profile_data_swiftshader',
    arbitrary_tester(),
    api.properties(
      swarm_hashes={
        'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
      },
      isolate_profile_data=True,
      test_gpu_dimension='none',
    ),
    api.post_check(
      api.swarming.check_triggered_request,
      'test_pre_run (with patch).[trigger] base_unittests on '
      'SwiftShader GPU on Linux (with patch)',
      lambda check, req: check('LLVM_PROFILE_FILE' in req[0].env_vars),
    ),
    api.post_process(
      post_process.StepCommandContains,
      'base_unittests on SwiftShader GPU on Linux (with patch)',
      [
        (
          '[CACHE]/builder/src/testing/merge_scripts/code_coverage/'
          'merge_results.py'
        )
      ],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'fail',
    arbitrary_tester(),
    api.properties(
      swarm_hashes={
        'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
      },
    ),
    api.step_data(
      'test_pre_run (with patch).[trigger] base_unittests on Intel GPU on '
      'Linux (with patch)',
      retcode=1,
    ),
    api.expect_status('INFRA_FAILURE'),
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
    api.expect_status('INFRA_FAILURE'),
    api.post_process(
      post_process.SummaryMarkdown,
      "Infra Failure: "
      "Step('test_pre_run (with patch).[trigger] base_unittests "
      "on Intel GPU on Linux (with patch)') (retcode: 1)",
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'fail_to_trigger',
    arbitrary_tester(),
    api.expect_status('INFRA_FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'without_patch_filter',
    arbitrary_tester(),
    api.properties(
      swarm_hashes={
        'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
      },
      run_without_patch='a',
    ),
    api.chromium_tests.gen_swarming_and_rdb_results(
      'base_unittests',
      'with patch',
      extra_suffix='on Intel GPU on Linux',
      failures=['Test.Two'],
    ),
    api.post_process(
      post_process.MustRun,
      '[trigger] base_unittests on Intel GPU on Linux (without patch)',
    ),
    api.post_process(
      api.swarming.check_triggered_request,
      '[trigger] base_unittests on Intel GPU on Linux (without patch)',
      lambda check, req: check(
        '--isolated-script-test-filter=Test.Two' in req[0].command
      ),
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'expected_failures',
    arbitrary_tester(),
    api.properties(
      swarm_hashes={
        'blink_web_tests': 'ffffffffffffffffffffffffffffffffffffffff/size',
      },
      test_filter=['test1', 'test2'],
      repeat_count=20,
    ),
    api.chromium_tests.gen_swarming_and_rdb_results(
      'blink_web_tests',
      'with patch',
      extra_suffix='on Intel GPU on Linux',
      failures=['Test1', 'Test2', 'Test3', 'Test4'],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'customized_test_options',
    arbitrary_tester(),
    api.properties(
      swarm_hashes={
        'blink_web_tests': 'ffffffffffffffffffffffffffffffffffffffff/size',
      },
      test_filter=['test1', 'test2'],
      repeat_count=20,
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'angle_unittests_options',
    arbitrary_tester(),
    api.properties(
      swarm_hashes={
        'angle_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
      },
      test_filter=['test1', 'test2'],
      repeat_count=20,
    ),
    api.post_process(post_process.DropExpectation),
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
      },
    ),
    api.post_process(
      api.swarming.check_triggered_request,
      'test_pre_run (with patch).[trigger] base_unittests '
      'on Intel GPU on Windows (with patch)',
      lambda check, req: check(('gpu', '8086') in req[0].dimensions.items()),
      lambda check, req: check(('os', 'Windows') in req[0].dimensions.items()),
    ),
    api.post_process(post_process.DropExpectation),
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
      },
    ),
    api.post_process(
      api.swarming.check_triggered_request,
      'test_pre_run (with patch).[trigger] base_unittests '
      'on Intel GPU on Mac (with patch)',
      lambda check, req: check(('gpu', '8086') in req[0].dimensions.items()),
      lambda check, req: check(('os', 'Mac') in req[0].dimensions.items()),
    ),
    api.post_process(post_process.DropExpectation),
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
      },
    ),
    api.post_process(
      api.swarming.check_triggered_request,
      'test_pre_run (with patch).[trigger] base_unittests '
      'on Intel GPU on Mac Retina (with patch)',
      lambda check, req: check(('gpu', '8086') in req[0].dimensions.items()),
      lambda check, req: check(('os', 'Mac') in req[0].dimensions.items()),
      lambda check, req: check(('hidpi', '1') in req[0].dimensions.items()),
    ),
    api.post_process(post_process.DropExpectation),
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
      },
    ),
    api.post_process(
      api.swarming.check_triggered_request,
      'test_pre_run (with patch).[trigger] base_unittests '
      'on Android device Nexus 5X (with patch)',
      lambda check, req: check(
        ('device_type', 'bullhead') in req[0].dimensions.items()
      ),
      lambda check, req: check(
        ('device_os', 'LOL123') in req[0].dimensions.items()
      ),
      lambda check, req: check(('os', 'Android') in req[0].dimensions.items()),
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'invalid_test_results',
    arbitrary_tester(),
    api.properties(
      swarm_hashes={
        'blink_web_tests': 'ffffffffffffffffffffffffffffffffffffffff/size',
      },
      test_filter=['test1', 'test2'],
      repeat_count=20,
    ),
    api.override_step_data(
      'blink_web_tests on Intel GPU on Linux (with patch)',
      api.chromium_swarming.canned_summary_output(
        api.test_utils.m.json.output(None, 255), shards=2
      ),
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'shard_timed_out_failure',
    arbitrary_tester(),
    api.properties(
      swarm_hashes={
        'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
      }
    ),
    api.override_step_data(
      'base_unittests on Intel GPU on Linux (with patch)',
      api.chromium_swarming.summary(
        dispatched_task_step_test_data=None,
        raw_summary={
          'shards': [
            {
              'created_ts': '2014-09-25T01:41:00.123',
              'started_ts': '2014-09-25T01:42:11.123',
              'completed_ts': '2014-09-25T01:43:11.123',
              'duration': 31.5,
              'state': 'TIMED_OUT',
            }
          ]
        },
      ),
    ),
    api.post_process(post_process.DropExpectation),
  )
