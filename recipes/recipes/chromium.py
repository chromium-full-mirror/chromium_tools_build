# Copyright 2013 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
  chromium,
  chromium_android,
  chromium_swarming,
  chromium_tests,
  chromium_tests_builder_config,
  chromium_turboci,
  isolate,
  test_utils,
)
from RECIPE_MODULES.depot_tools import bot_update, gsutil
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  commit_position,
  file,
  json,
  legacy_annotation,
  path,
  platform,
  properties,
  raw_io,
  runtime,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  bot_update: bot_update.API
  buildbucket: buildbucket.API
  chromium: chromium.API
  chromium_android: chromium_android.API
  chromium_swarming: chromium_swarming.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  chromium_turboci: chromium_turboci.API
  commit_position: commit_position.API
  file: file.API
  gsutil: gsutil.API
  isolate: isolate.API
  json: json.API
  legacy_annotation: legacy_annotation.API
  path: path.API
  platform: platform.API
  properties: properties.API
  raw_io: raw_io.API
  runtime: runtime.API
  step: step.API
  test_utils: test_utils.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API
  chromium_swarming: chromium_swarming.TEST_API
  chromium_tests: chromium_tests.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  json: json.TEST_API
  platform: platform.TEST_API
  properties: properties.TEST_API
  raw_io: raw_io.TEST_API
  test_utils: test_utils.TEST_API


def RunSteps(api: DEPS):
  builder_id, builder_config = (
    api.chromium_tests_builder_config.lookup_builder()
  )
  with (
    api.chromium.chromium_layout(),
    api.chromium_turboci.display_turboci_checks(),
  ):
    build_result, _ = api.chromium_tests.main_waterfall_steps(
      builder_id, builder_config
    )
    return build_result


def GenTests(api: TEST_DEPS):
  ctbc_api = api.chromium_tests_builder_config

  yield api.test(
    'dynamic_swarmed_gtest',
    api.platform('linux', 64),
    api.chromium.ci_build(
      builder_group='fake-group',
      builder='fake-builder',
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_ci_builder(
        builder_group='fake-group',
        builder='fake-builder',
      ).assemble()
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-builder': {
          'gtest_tests': [
            {
              'test': 'browser_tests',
              'resultdb': {'enable': True},
              'swarming': {
                'dimensions': {
                  'os': 'Linux',
                },
              },
            },
          ],
        },
      },
    ),
  )

  # The chromium.gpu.fyi bots use serialize_tests in order to reduce load on the
  # GPU bots in the Swarming pool.
  yield api.test(
    'dynamic_swarmed_serialized_gtests',
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
    api.properties(
      swarm_hashes={
        'base_unittests': 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeee/size',
        'browser_tests': 'ffffffffffffffffffffffffffffff/size',
      }
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-tester': {
          'gtest_tests': [
            {
              'test': 'base_unittests',
              'swarming': {
                'dimensions': {
                  'gpu': '10de:104a',  # NVIDIA GeForce GT 610
                  'os': 'Linux',
                },
              },
            },
            {
              'test': 'browser_tests',
              'swarming': {
                'dimensions': {
                  'gpu': '10de:104a',  # NVIDIA GeForce GT 610
                  'os': 'Linux',
                },
              },
            },
          ],
        },
      },
    ),
    # Make one of the tests fail to improve code coverage.
    api.override_step_data(
      'base_unittests on NVIDIA 0x104a GPU on Linux',
      api.chromium_swarming.canned_summary_output(
        api.test_utils.canned_gtest_output(False)
      ),
    ),
  )

  yield api.test(
    'dynamic_swarmed_gtest_mac_gpu',
    api.chromium.ci_build(
      builder_group='fake-group',
      builder='fake-tester',
      parent_buildername='fake-builder',
    ),
    api.platform('mac', 64),
    api.chromium_tests_builder_config.properties(
      api.chromium_tests_builder_config.properties_assembler_for_ci_tester(
        builder_group='fake-group',
        builder='fake-tester',
      )
      .with_parent(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.properties(
      swarm_hashes={
        'gl_tests': 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeee/size',
      }
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-tester': {
          'gtest_tests': [
            {
              'name': 'gl_tests intel',
              'test': 'gl_tests',
              'swarming': {
                'dimensions': {
                  'gpu': '8086:0a2e',  # Intel Iris
                  'hidpi': '0',
                  'os': 'Mac-10.10',
                },
              },
            },
            {
              'name': 'gl_tests nvidia',
              'test': 'gl_tests',
              'swarming': {
                'dimensions': {
                  'gpu': '10de:0fe9',  # NVIDIA GeForce GT750M
                  'hidpi': '1',
                  'os': 'Mac-10.13',
                },
              },
            },
          ],
        },
      },
    ),
  )

  yield api.test(
    'build_dynamic_isolated_script_test',
    api.platform('linux', 64),
    api.chromium.ci_build(
      builder_group='fake-group',
      builder='fake-builder',
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_ci_builder(
        builder_group='fake-group',
        builder='fake-builder',
      ).assemble()
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-builder': {
          'isolated_scripts': [
            {
              'test': 'telemetry_gpu_unittests',
              'name': 'telemetry_gpu_unittests',
            },
          ],
        },
      },
    ),
  )

  yield api.test(
    'dynamic_isolated_script_test',
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
    api.properties(
      swarm_hashes={
        'telemetry_gpu_unittests': 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeee/size',
      }
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-tester': {
          'isolated_scripts': [
            {
              'test': 'telemetry_gpu_unittests',
              'name': 'telemetry_gpu_unittests',
              'args': ['--correct-common-arg'],
              'precommit_args': ['--SHOULD-NOT-BE-PRESENT-DURING-THE-RUN'],
              'non_precommit_args': [
                '--these-args-should-be-present',
                '--test-machine-name="${buildername}"',
                '--build-revision="${got_revision}"',
              ],
            },
          ],
        },
      },
    ),
  )

  yield api.test(
    'dynamic_local_isolated_script_test_with_failed_json_results',
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
    api.properties(
      swarm_hashes={
        'telemetry_gpu_unittests': 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeee/size',
      }
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-tester': {
          'isolated_scripts': [
            {
              'test': 'telemetry_gpu_unittests',
              'name': 'telemetry_gpu_unittests',
            },
          ],
        },
      },
    ),
    api.override_step_data(
      'telemetry_gpu_unittests results',
      stdout=api.raw_io.output_text(
        api.test_utils.rdb_results(
          'telemetry_gpu_unittests', failing_tests=['Test.One']
        )
      ),
    ),
    api.expect_status('FAILURE'),
  )

  yield api.test(
    'dynamic_local_isolated_script_test_with_passed_json_results',
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
    api.properties(
      swarm_hashes={
        'telemetry_gpu_unittests': 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeee/size',
      }
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-tester': {
          'isolated_scripts': [
            {
              'test': 'telemetry_gpu_unittests',
              'name': 'telemetry_gpu_unittests',
            },
          ],
        },
      },
    ),
  )

  yield api.test(
    'dynamic_local_isolated_script_test_with_custom_results_handler',
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
    api.properties(
      swarm_hashes={
        'telemetry_gpu_unittests': 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeee/size',
      }
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-tester': {
          'isolated_scripts': [
            {
              'test': 'telemetry_gpu_unittests',
              'name': 'telemetry_gpu_unittests',
              'results_handler': 'fake',
            },
          ],
        },
      },
    ),
    api.override_step_data(
      'telemetry_gpu_unittests results',
      stdout=api.raw_io.output_text(
        api.test_utils.rdb_results(
          'telemetry_gpu_unittests', failing_tests=['Test.One']
        )
      ),
    ),
    api.expect_status('FAILURE'),
  )

  yield api.test(
    'dynamic_isolated_script_test_harness_failure_no_json',
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
    api.properties(
      swarm_hashes={
        'telemetry_gpu_unittests': 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeee/size',
      }
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-tester': {
          'isolated_scripts': [
            {
              'test': 'telemetry_gpu_unittests',
              'name': 'telemetry_gpu_unittests',
            },
          ],
        },
      },
    ),
    api.override_step_data(
      'telemetry_gpu_unittests', api.json.output(None), retcode=-11
    ),
    api.expect_status('FAILURE'),
  )

  yield api.test(
    'build_dynamic_swarmed_isolated_script_test',
    api.platform('linux', 64),
    api.chromium.ci_build(
      builder_group='fake-group',
      builder='fake-builder',
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_ci_builder(
        builder_group='fake-group',
        builder='fake-builder',
      ).assemble()
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-builder': {
          'isolated_scripts': [
            {
              'test': 'telemetry_gpu_unittests',
              'name': 'telemetry_gpu_unittests',
              'swarming': {
                'dimensions': {
                  'os': 'Linux',
                },
              },
            },
          ],
        },
      },
    ),
  )

  yield api.test(
    'dynamic_swarmed_passed_isolated_script_test',
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
    api.properties(
      swarm_hashes={
        'telemetry_gpu_unittests': 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeee/size',
      }
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-tester': {
          'isolated_scripts': [
            {
              'test': 'telemetry_gpu_unittests',
              'name': 'telemetry_gpu_unittests',
              'swarming': {
                'dimensions': {
                  'os': 'Linux',
                },
              },
            },
          ],
        },
      },
    ),
  )

  yield api.test(
    'dynamic_swarmed_sharded_passed_isolated_script_test',
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
    api.properties(
      version='v23523',
      swarm_hashes={
        'telemetry_gpu_unittests': 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeee/size'
      },
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-tester': {
          'isolated_scripts': [
            {
              'test': 'telemetry_gpu_unittests',
              'name': 'telemetry_gpu_unittests',
              'swarming': {
                'dimensions': {
                  'os': 'Linux',
                },
                'shards': 2,
              },
            },
          ],
        },
      },
    ),
  )

  yield api.test(
    'dynamic_swarmed_sharded_failed_isolated_script_test',
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
    api.properties(
      swarm_hashes={
        'telemetry_gpu_unittests': 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeee/size',
      }
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-tester': {
          'isolated_scripts': [
            {
              'test': 'telemetry_gpu_unittests',
              'name': 'telemetry_gpu_unittests',
              'swarming': {
                'dimensions': {
                  'os': 'Linux',
                },
                'shards': 2,
              },
            },
          ],
        },
      },
    ),
    api.chromium_tests.gen_swarming_and_rdb_results(
      'telemetry_gpu_unittests', '', failures=['Test.One']
    ),
    api.expect_status('FAILURE'),
  )

  yield api.test(
    'dynamic_swarmed_sharded_isolated_chartjson_test_disabled',
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
    api.properties(
      swarm_hashes={
        'telemetry_gpu_unittests': 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeee/size',
      }
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-tester': {
          'isolated_scripts': [
            {
              'test': 'telemetry_gpu_unittests',
              'name': 'telemetry_gpu_unittests',
              'swarming': {
                'dimensions': {
                  'os': 'Linux',
                },
                'shards': 2,
              },
            },
          ],
        },
      },
    ),
  )

  yield api.test(
    'dynamic_swarmed_sharded_isolated_chartjson_test_missing_all_shards',
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
    api.properties(
      swarm_hashes={
        'telemetry_gpu_unittests': 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeee/size',
      }
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-tester': {
          'isolated_scripts': [
            {
              'test': 'telemetry_gpu_unittests',
              'name': 'telemetry_gpu_unittests',
              'swarming': {
                'dimensions': {
                  'os': 'Linux',
                },
                'shards': 2,
              },
            },
          ],
        },
      },
    ),
    api.chromium_tests.gen_swarming_and_rdb_results(
      'telemetry_gpu_unittests', '', failures=['Test.One']
    ),
    api.post_process(
      post_process.DoesNotRun, 'telemetry_gpu_unittests Dashboard Upload'
    ),
    api.post_process(post_process.Filter('telemetry_gpu_unittests')),
    api.expect_status('FAILURE'),
  )

  yield api.test(
    'dynamic_swarmed_isolated_script_test_linux_gpu',
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
    api.properties(
      swarm_hashes={
        'telemetry_gpu_unittests': 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeee/size',
      }
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-tester': {
          'isolated_scripts': [
            {
              'test': 'telemetry_gpu_unittests',
              'name': 'telemetry_gpu_unittests',
              'swarming': {
                'dimensions': {
                  'gpu': '10de:104a-1234',  # NVIDIA GPU.
                  'os': 'Linux',
                },
              },
            },
          ],
        },
      },
    ),
  )

  yield api.test(
    'dynamic_swarmed_isolated_script_test_linux_multi_gpu_no_driver',
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
    api.properties(
      swarm_hashes={
        'telemetry_gpu_unittests': 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeee/size',
      }
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-tester': {
          'isolated_scripts': [
            {
              'test': 'telemetry_gpu_unittests',
              'name': 'telemetry_gpu_unittests',
              'swarming': {
                'dimensions': {
                  'gpu': '10de:104a|10de:1234',  # NVIDIA GPUs.
                  'os': 'Linux',
                },
              },
            },
          ],
        },
      },
    ),
    api.post_process(
      post_process.MustRun,
      'test_pre_run.[trigger] telemetry_gpu_unittests on NVIDIA '
      '0x104a/NVIDIA 0x1234 GPU on Linux',
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'dynamic_swarmed_isolated_script_test_mac_gpu',
    api.chromium.ci_build(
      builder_group='fake-group',
      builder='fake-tester',
      parent_buildername='fake-builder',
    ),
    api.platform('mac', 64),
    api.chromium_tests_builder_config.properties(
      api.chromium_tests_builder_config.properties_assembler_for_ci_tester(
        builder_group='fake-group',
        builder='fake-tester',
      )
      .with_parent(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.properties(
      swarm_hashes={
        'telemetry_gpu_unittests': 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeee/size',
      }
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-tester': {
          'isolated_scripts': [
            {
              'test': 'telemetry_gpu_unittests',
              'name': 'telemetry_gpu_unittests intel',
              'swarming': {
                'dimensions': {
                  'gpu': '8086:0a2e',  # Intel Iris
                  'hidpi': '0',
                  'os': 'Mac-10.10',
                },
              },
            },
            {
              'test': 'telemetry_gpu_unittests',
              'name': 'telemetry_gpu_unittests nvidia',
              'swarming': {
                'dimensions': {
                  'gpu': '10de:0fe9',  # NVIDIA GeForce GT750M
                  'hidpi': '1',
                  'os': 'Mac-10.13',
                },
              },
            },
          ],
        },
      },
    ),
  )

  yield api.test(
    'dynamic_swarmed_isolated_script_test_win_gpu',
    api.chromium.ci_build(
      builder_group='fake-group',
      builder='fake-tester',
      parent_buildername='fake-builder',
    ),
    api.platform('win', 64),
    api.chromium_tests_builder_config.properties(
      api.chromium_tests_builder_config.properties_assembler_for_ci_tester(
        builder_group='fake-group',
        builder='fake-tester',
      )
      .with_parent(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.properties(
      swarm_hashes={
        'telemetry_gpu_unittests': 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeee/size',
      }
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-tester': {
          'isolated_scripts': [
            {
              'test': 'telemetry_gpu_unittests',
              'name': 'telemetry_gpu_unittests nvidia',
              'swarming': {
                'dimensions': {
                  # NVIDIA GeForce GT 610
                  'gpu': '10de:104a',
                  'os': 'Windows',
                },
              },
            },
            {
              'test': 'telemetry_gpu_unittests',
              'name': 'telemetry_gpu_unittests amd',
              'swarming': {
                'dimensions': {
                  # AMD Radeon HD 6450
                  'gpu': '1002:6779',
                  'os': 'Windows',
                },
              },
            },
            {
              'test': 'telemetry_gpu_unittests',
              'name': 'telemetry_gpu_unittests vmware',
              'swarming': {
                'dimensions': {
                  # VMWare SVGA II Adapter
                  'gpu': '15ad:0405',
                  'os': 'Windows',
                },
              },
            },
          ],
        },
      },
    ),
  )

  yield api.test(
    'dynamic_swarmed_isolated_script_test_win_non_gpu',
    api.chromium.ci_build(
      builder_group='fake-group',
      builder='fake-tester',
      parent_buildername='fake-builder',
    ),
    api.platform('win', 64),
    api.chromium_tests_builder_config.properties(
      api.chromium_tests_builder_config.properties_assembler_for_ci_tester(
        builder_group='fake-group',
        builder='fake-tester',
      )
      .with_parent(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.properties(
      swarm_hashes={
        'telemetry_gpu_unittests': 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeee/size',
      }
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-tester': {
          'isolated_scripts': [
            {
              'test': 'telemetry_gpu_unittests',
              'name': 'telemetry_gpu_unittests',
              'swarming': {
                'dimensions': {
                  'os': 'Windows',
                },
              },
            },
          ],
        },
      },
    ),
  )

  yield api.test(
    'dynamic_swarmed_failed_isolated_script_test',
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
    api.properties(
      swarm_hashes={
        'telemetry_gpu_unittests': 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeee/size',
      }
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-tester': {
          'isolated_scripts': [
            {
              'test': 'telemetry_gpu_unittests',
              'name': 'telemetry_gpu_unittests',
              'swarming': {
                'dimensions': {
                  'os': 'Linux',
                },
              },
            },
          ],
        },
      },
    ),
    api.chromium_tests.gen_swarming_and_rdb_results(
      'telemetry_gpu_unittests', '', failures=['Test.One']
    ),
    api.expect_status('FAILURE'),
  )

  yield api.test(
    'dynamic_swarmed_passed_with_bad_retcode_isolated_script_test',
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
    api.properties(
      swarm_hashes={
        'telemetry_gpu_unittests': 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeee/size',
      }
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-tester': {
          'isolated_scripts': [
            {
              'test': 'telemetry_gpu_unittests',
              'name': 'telemetry_gpu_unittests',
              'swarming': {
                'dimensions': {
                  'os': 'Linux',
                },
              },
            },
          ],
        },
      },
    ),
    api.override_step_data(
      'telemetry_gpu_unittests',
      api.chromium_swarming.canned_summary_output(
        api.json.output({}), failure=True, retcode=255
      ),
    ),
    api.expect_status('FAILURE'),
  )

  yield api.test(
    'dynamic_gtest_on_builder',
    api.platform('linux', 64),
    api.chromium.ci_build(
      builder_group='fake-group',
      builder='fake-builder',
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_ci_builder(
        builder_group='fake-group',
        builder='fake-builder',
      ).assemble()
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-tester': {
          'gtest_tests': [
            {
              'test': 'base_unittests',
            },
            {
              'test': 'browser_tests',
            },
          ],
        },
      },
    ),
  )

  yield api.test(
    'dynamic_local_gtest_win',
    api.chromium.ci_build(
      builder_group='fake-group',
      builder='fake-builder',
    ),
    api.platform('win', 64),
    api.chromium_tests_builder_config.properties(
      api.chromium_tests_builder_config.properties_assembler_for_ci_builder(
        builder_group='fake-group',
        builder='fake-builder',
        builder_spec=ctbc.BuilderSpec.create(
          gclient_config='chromium',
          chromium_config='chromium',
        ),
      ).assemble()
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-builder': {
          'gtest_tests': [
            {
              'test': 'aura_unittests',
            },
            {
              'test': 'browser_tests',
            },
          ],
        },
      },
    ),
  )

  yield api.test(
    'buildnumber_zero',
    api.platform('linux', 64),
    api.chromium.ci_build(
      builder_group='fake-group',
      builder='fake-builder',
      build_number=0,
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_ci_builder(
        builder_group='fake-group',
        builder='fake-builder',
        builder_spec=ctbc.BuilderSpec.create(
          gclient_config='chromium',
          chromium_config='chromium',
        ),
      ).assemble()
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-builder': {
          'gtest_tests': [
            {
              'test': 'base_unittests',
            },
          ],
        },
      },
    ),
  )

  yield api.test(
    'dynamic_local_script_test_failure',
    api.platform('linux', 64),
    api.chromium.ci_build(
      builder_group='fake-group',
      builder='fake-tester',
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_ci_builder(
        builder_group='fake-group',
        builder='fake-tester',
        builder_spec=ctbc.BuilderSpec.create(
          gclient_config='chromium',
          chromium_config='chromium',
        ),
      ).assemble()
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-tester': {
          'scripts': [
            {
              'name': 'test_script_with_broken_tests',
              'script': 'test_script_with_broken_tests.py',
            }
          ]
        }
      },
    ),
    api.override_step_data(
      'test_script_with_broken_tests results',
      stdout=api.raw_io.output_text(
        api.test_utils.rdb_results(
          'test_script_with_broken_tests',
          failing_tests=['FailSuite.Test1', 'FlakySuite.TestA'],
        )
      ),
    ),
    api.expect_status('FAILURE'),
  )

  yield api.test(
    'gtest_custom_merge_script',
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
    api.properties(
      swarm_hashes={
        'browser_tests': 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeee/size',
      }
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-tester': {
          'gtest_tests': [
            {
              'test': 'browser_tests',
              'swarming': {
                'dimensions': {
                  'os': 'Linux',
                },
              },
              'merge': {
                'script': '//fake_merge_script.py',
              },
            },
          ],
        },
      },
    ),
    api.post_process(post_process.Filter('browser_tests')),
  )

  yield api.test(
    'gtest_bad_custom_merge_script',
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
    api.properties(
      swarm_hashes={
        'browser_tests': 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeee/size',
      }
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-tester': {
          'gtest_tests': [
            {
              'test': 'browser_tests',
              'swarming': {
                'dimensions': {
                  'os': 'Linux',
                },
              },
              'merge': {
                'script': 'fake_merge_script.py',
              },
            },
          ],
        },
      },
    ),
    api.expect_status('FAILURE'),
  )

  yield api.test(
    'isolated_script_test_custom_merge_script',
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
    api.properties(
      swarm_hashes={
        'fake_test': 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeee/size',
      }
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-tester': {
          'isolated_scripts': [
            {
              'test': 'fake_test',
              'name': 'fake_test',
              'merge': {
                'script': '//fake_merge_script.py',
              },
              'swarming': {
                'dimensions': {
                  'os': 'Linux',
                },
              },
            },
          ],
        },
      },
    ),
    api.post_process(post_process.Filter('fake_test')),
  )

  yield api.test(
    'isolated_script_test_bad_custom_merge_script',
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
    api.properties(
      swarm_hashes={
        'fake_test': 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeee/size',
      }
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-tester': {
          'isolated_scripts': [
            {
              'test': 'fake_test',
              'name': 'fake_test',
              'merge': {
                'script': 'bad_fake_merge_script.py',
              },
              'swarming': {
                'dimensions': {
                  'os': 'Linux',
                },
              },
            },
          ],
        },
      },
    ),
    api.expect_status('FAILURE'),
  )

  yield api.test(
    'isolated_script_test_custom_merge_script_with_args',
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
    api.properties(
      swarm_hashes={
        'fake_test': 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeee/size',
      }
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-tester': {
          'isolated_scripts': [
            {
              'test': 'fake_test',
              'name': 'fake_test',
              'merge': {
                'script': '//fake_merge_script.py',
                'args': [
                  '--foo',
                  'foo_value',
                ],
              },
              'swarming': {
                'dimensions': {
                  'os': 'Linux',
                },
              },
            },
          ],
        },
      },
    ),
    api.post_process(post_process.Filter('fake_test')),
  )

  yield api.test(
    'isolated_script_test_custom_results_handler',
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
    api.properties(
      swarm_hashes={
        'fake_test': 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeee/size',
      }
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-tester': {
          'isolated_scripts': [
            {
              'test': 'fake_test',
              'name': 'fake_test',
              'results_handler': 'fake',
              'swarming': {
                'dimensions': {
                  'os': 'Linux',
                },
              },
            },
          ],
        },
      },
    ),
    api.post_process(post_process.Filter('fake_test')),
  )

  yield api.test(
    'isolated_script_test_invalid_results_handler',
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
    api.properties(
      swarm_hashes={
        'fake_test': 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeee/size',
      }
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-tester': {
          'isolated_scripts': [
            {
              'test': 'fake_test',
              'name': 'fake_test',
              'results_handler': 'unknown',
            },
          ],
        },
      },
    ),
    api.expect_status('FAILURE'),
  )
