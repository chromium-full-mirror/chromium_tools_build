# Copyright 2013 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import (Filter, DoesNotRun, DropExpectation,
                                        MustRun, StepCommandContains,
                                        StepTextContains)

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

DEPS = [
    'chromium',
    'chromium_android',
    'chromium_checkout',
    'chromium_swarming',
    'chromium_tests',
    'chromium_tests_builder_config',
    'chromium_turboci',
    'depot_tools/bot_update',
    'depot_tools/gclient',
    'depot_tools/gerrit',
    'depot_tools/tryserver',
    'filter',
    'isolate',
    'recipe_engine/buildbucket',
    'recipe_engine/commit_position',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/led',
    'recipe_engine/legacy_annotation',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/step',
    'recipe_engine/runtime',
    'test_utils',
]


def RunSteps(api):
  api.tryserver.require_is_tryserver()

  builder_id, builder_config = (
      api.chromium_tests_builder_config.lookup_builder())
  with api.chromium.chromium_layout(), \
       api.chromium_turboci.display_turboci_checks():
    return api.chromium_tests.trybot_steps(builder_id, builder_config)


def _sanitize_nonalpha(text):
  return ''.join(c if c.isalnum() else '_' for c in text)


def GenTests(api):
  ctbc_api = api.chromium_tests_builder_config

  def swarm_hashes(swarm_hashes=None, extra_swarmed_tests=None):
    swarm_hashes = dict(swarm_hashes or {})
    for test in extra_swarmed_tests or []:
      swarm_hashes[test] = '[dummy hash for %s/size]' % test
    return api.properties(swarm_hashes=swarm_hashes)

  yield api.test(
      'not-a-tryjob',
      api.chromium.generic_build(
          builder_group='fake-group',
          builder='fake-builder',
      ),
      api.post_check(MustRun, 'not a tryjob'),
      api.expect_status('INFRA_FAILURE'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'led-not-a-tryjob',
      api.chromium.generic_build(
          builder_group='fake-group',
          builder='fake-builder',
      ),
      api.properties(
          **{
              '$recipe_engine/led': {
                  'led_run_id': 'fake-run-id',
                  'isolated_input': {
                      'hash': 'fake-hash',
                  },
              },
          }),
      api.post_check(MustRun, 'not a tryjob'),
      api.post_check(StepTextContains, 'not a tryjob',
                     ["run 'led edit-cr-cl <source CL URL>'"]),
      api.expect_status('FAILURE'),
      api.post_process(DropExpectation),
  )

  # Regression test for http://crbug.com/453471#c16
  yield api.test(
      'clobber_analyze',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group', builder='fake-try-builder'),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
              builder_spec=ctbc.BuilderSpec.create(
                  gclient_config='chromium',
                  chromium_config='chromium',
                  chromium_apply_config=['clobber'],
              ),
          ).assemble()),
      api.filter.analyze_output(
          status='Found dependency',
          test_targets=[],
          compile_targets=['base_unittests', 'net_unittests'],
      ),
  )

  yield api.test(
      'local_gtest_with_invalid_results',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group', builder='fake-try-builder'),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.chromium_tests.read_targets_spec('fake-group', {
          'fake-builder': {
              'gtest_tests': [{
                  'test': 'base_unittests',
              }],
          },
      }),
      api.override_step_data(
          'base_unittests results',
          stdout=api.raw_io.output_text(
              api.test_utils.rdb_results(
                  'base_unittests', failing_tests=['Test.One']))),
      api.override_step_data('base_unittests (without patch)', retcode=1),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'dynamic_isolated_script_test_on_trybot_passing',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group', builder='fake-try-builder'),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      swarm_hashes(extra_swarmed_tests=['telemetry_gpu_unittests']),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'isolated_scripts': [{
                      'test': 'telemetry_gpu_unittests',
                      'name': 'telemetry_gpu_unittests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                          },
                      },
                  },],
              },
          }),
  )

  yield api.test(
      'dynamic_isolated_script_test_on_trybot_no_stdout',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
      ),
      api.chromium_tests_builder_config.properties(
          api.chromium_tests_builder_config
          .properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      swarm_hashes(extra_swarmed_tests=['telemetry_gpu_unittests']),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'isolated_scripts': [{
                      'test': 'telemetry_gpu_unittests',
                      'name': 'telemetry_gpu_unittests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                          },
                      },
                  },],
              },
          }),
      api.post_process(StepCommandContains,
                       'telemetry_gpu_unittests (with patch)',
                       ['-task-output-stdout', 'none']),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'dynamic_isolated_script_test_on_trybot_failing',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group', builder='fake-try-builder'),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      swarm_hashes(extra_swarmed_tests=['telemetry_gpu_unittests']),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'isolated_scripts': [{
                      'test': 'telemetry_gpu_unittests',
                      'name': 'telemetry_gpu_unittests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                          },
                      },
                  },],
              },
          }),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'telemetry_gpu_unittests', 'with patch', failures=['Test.One']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'telemetry_gpu_unittests',
          'retry shards with patch',
          failures=['Test.One']),
      api.chromium_tests.gen_swarming_and_rdb_results('telemetry_gpu_unittests',
                                                      'without patch'),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'dynamic_isolated_script_test_with_args_on_trybot',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group', builder='fake-try-builder'),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      swarm_hashes(extra_swarmed_tests=['telemetry_gpu_unittests']),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'isolated_scripts': [{
                      'test': 'telemetry_gpu_unittests',
                      'name': 'telemetry_gpu_unittests',
                      'args': ['--correct-common-arg'],
                      'non_precommit_args':
                          ['--SHOULD-NOT-BE-PRESENT-DURING-THE-RUN'],
                      'precommit_args': [
                          '--these-args-should-be-present',
                          '--test-machine-name=\"${buildername}\"',
                          '--build-revision=\"${got_revision}\"',
                      ],
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                          },
                      },
                  },],
              },
          }),
  )

  yield api.test(
      'dynamic_swarmed_isolated_script_test_failure_no_result_json',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group', builder='fake-try-builder'),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      swarm_hashes(extra_swarmed_tests=['telemetry_gpu_unittests']),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'isolated_scripts': [{
                      'test': 'telemetry_gpu_unittests',
                      'name': 'telemetry_gpu_unittests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                          },
                      },
                  },],
              },
          }),
      api.override_step_data(
          'telemetry_gpu_unittests (with patch)',
          api.chromium_swarming.canned_summary_output(
              api.json.output({}), failure=True, retcode=1)),
      api.override_step_data(
          'telemetry_gpu_unittests (retry shards with patch)',
          api.chromium_swarming.canned_summary_output(
              api.json.output({}), failure=True, retcode=1)),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'swarming_test_with_priority_expiration_and_timeout',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group', builder='fake-try-builder'),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      swarm_hashes(extra_swarmed_tests=['gl_tests']),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'gl_tests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                          },
                          'expiration': 7200,
                          'hard_timeout': 1800,
                      },
                  },],
              },
          }),
  )

  yield api.test(
      'swarming_trigger_failure',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group', builder='fake-try-builder'),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      # Specifying empty swarm hashes will override the value of
      # isolate.isolated_tests so that when we attempt to run base_unittests
      # we get an error
      swarm_hashes(),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'base_unittests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                          },
                      },
                  },],
              },
          }),
      api.expect_status('INFRA_FAILURE'),
  )

  yield api.test(
      'swarming_test_failure',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group', builder='fake-try-builder'),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      swarm_hashes(extra_swarmed_tests=['gl_tests']),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'gl_tests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                          },
                      },
                  },],
              },
          }),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'gl_tests', 'with patch', failures=['Test.One']),
  )

  yield api.test(
      'swarming_test_failure_no_patch_deapplication',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group', builder='fake-try-builder'),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      swarm_hashes(extra_swarmed_tests=['gl_tests']),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'gl_tests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                          },
                      },
                  },],
              },
          }),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'gl_tests', 'with patch', failures=['Test.One']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'gl_tests', 'retry shards with patch', failures=['Test.One']),
      api.override_step_data(
          'git diff to analyze patch',
          api.raw_io.stream_output('foo.cc\ntesting/buildbot/bar.json')),
      api.expect_status('FAILURE'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'compile_failure_without_patch_deapply_fn',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group', builder='fake-try-builder'),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.chromium_tests.read_targets_spec('fake-group', {
          'fake-builder': {
              'gtest_tests': [{
                  'test': 'base_unittests',
              }],
          },
      }),
      api.override_step_data(
          'base_unittests results',
          stdout=api.raw_io.output_text(
              api.test_utils.rdb_results(
                  'base_unittests', failing_tests=['Test.One']))),
      api.step_data('compile (without patch)',
                    api.legacy_annotation.infra_failure_step),
      api.expect_status('FAILURE'),
  )

  for step, status in (
      ('bot_update', 'INFRA_FAILURE'),
      ('gclient runhooks (with patch)', 'FAILURE'),
  ):
    yield api.test(
        _sanitize_nonalpha(step) + '_failure',
        api.platform('linux', 64),
        api.chromium.try_build(
            builder_group='fake-try-group', builder='fake-try-builder'),
        ctbc_api.properties(ctbc_api.properties_assembler_for_try_builder()
                            .with_mirrored_builder(
                                builder_group='fake-group',
                                builder='fake-builder',
                            ).assemble()),
        api.step_data(step, retcode=1),
        api.expect_status(status),
    )

  yield api.test(
      'compile_failure_ng',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group', builder='fake-try-builder'),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.chromium_tests.read_targets_spec('fake-group', {
          'fake-builder': {
              'gtest_tests': [{
                  'test': 'base_unittests',
              }],
          },
      }),
      api.step_data('compile (with patch)', retcode=1),
      api.expect_status('FAILURE'),
  )

  # Test that the component rev for v8 is correctly applied
  # both on the initial checkout and after deapplying the patch.
  yield api.test(
      'compile_failure_with_component_rev',
      api.chromium_tests_builder_config.try_build(
          project='v8',
          builder_group='tryserver.v8',
          builder='v8_linux_chromium_gn_rel',
          git_repo='https://chromium.googlesource.com/v8/v8',
      ),
      api.chromium_tests.read_targets_spec('client.v8.fyi', {
          'V8 Linux GN': {
              'additional_compile_targets': ['base_unittests'],
          },
      }),
      api.step_data('compile (with patch)', retcode=1),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'compile_failure_without_patch_ng',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group', builder='fake-try-builder'),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.chromium_tests.read_targets_spec('fake-group', {
          'fake-builder': {
              'gtest_tests': [{
                  'test': 'base_unittests',
              }],
          },
      }),
      api.step_data('compile (with patch)', retcode=1),
      api.expect_status('FAILURE'),
  )

  # Successfully compiling, isolating and running two targets on swarming for a
  # commit queue job.
  yield api.test(
      'swarming_basic_cq',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group', builder='fake-try-builder'),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      swarm_hashes(extra_swarmed_tests=['base_unittests', 'browser_tests']),
  )

  # Successfully compiling, isolating and running two targets on swarming for a
  # manual try job.
  yield api.test(
      'swarming_basic_try_job',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group', builder='fake-try-builder'),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      swarm_hashes(extra_swarmed_tests=['base_unittests', 'browser_tests']),
  )

  # One target (browser_tests) failed to produce *.isolated file.
  yield api.test(
      'swarming_missing_isolated',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group', builder='fake-try-builder'),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      swarm_hashes(extra_swarmed_tests=['base_unittests']),
  )

  # Does not result in a compile
  yield api.test(
      'no_compile_because_of_analyze',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group', builder='fake-try-builder'),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.chromium_tests.read_targets_spec('fake-group', {
          'fake-builder': {
              'gtest_tests': [{
                  'test': 'base_unittests',
              }],
          },
      }),
      api.filter.no_dependency(),
      api.post_process(DoesNotRun, 'compile (with patch)'),
      api.post_process(Filter('analyze')),
  )

  # This should result in a compile.
  yield api.test(
      'compile_because_of_analyze_matching_exclusion',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group', builder='fake-try-builder'),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.chromium_tests.read_targets_spec('fake-group', {}),
      api.filter.exclude_everything(),
      api.chromium_tests.read_targets_spec('fake-group', {
          'fake-builder': {
              'gtest_tests': [{
                  'test': 'base_unittests',
              }],
          },
      }),
      api.post_process(Filter('analyze', 'compile (with patch)')),
  )

  # This should result in a compile.
  yield api.test(
      'compile_because_of_analyze',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group', builder='fake-try-builder'),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.chromium_tests.read_targets_spec('fake-group', {}),
      api.filter.analyze_output(
          status='Found dependency',
          compile_targets=['browser_tests'],
          test_targets=[],
      ),
      api.post_process(Filter('analyze', 'compile (with patch)')),
  )

  # Tests compile_targets portion of analyze module with filtered tests
  yield api.test(
      'compile_because_of_analyze_with_filtered_tests',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group', builder='fake-try-builder'),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.filter.analyze_output(
          status='Found dependency',
          test_targets=['browser_tests', 'base_unittests'],
          compile_targets=['browser_tests', 'base_unittests'],
      ),
      api.post_process(Filter('analyze', 'compile (with patch)')),
  )

  # Tests compile_target portion of analyze module with filtered compile targets
  yield api.test(
      'compile_because_of_analyze_with_filtered_compile_targets',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group', builder='fake-try-builder'),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.filter.analyze_output(
          status='Found dependency',
          test_targets=['browser_tests', 'base_unittests'],
          compile_targets=['chrome', 'browser_tests', 'base_unittests'],
      ),
      api.post_process(Filter('analyze', 'compile (with patch)')),
  )

  # Tests compile_targets portion of analyze with a bot that doesn't include the
  # 'all' target.
  yield api.test(
      'compile_because_of_analyze_with_filtered_compile_targets_exclude_all',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group', builder='fake-try-builder'),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.chromium_tests.read_targets_spec('fake-group', {
          'fake-builder': {
              'gtest_tests': [{
                  'test': 'base_unittests',
              }],
          },
      }),
      api.filter.analyze_output(
          status='Found dependency',
          test_targets=['browser_tests', 'base_unittests'],
          compile_targets=['base_unittests'],
      ),
      api.post_process(Filter('analyze', 'compile (with patch)')),
  )

  # Tests compile_targets portion of analyze with a bot that doesn't include the
  # 'all' target.
  yield api.test(
      'analyze_finds_invalid_target',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group', builder='fake-try-builder'),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.chromium_tests.read_targets_spec('fake-group', {
          'fake-builder': {
              'gtest_tests': [{
                  'test': 'base_unittests',
              }],
          },
      }),
      api.override_step_data(
          'analyze',
          api.json.output(
              {'invalid_targets': ['invalid target', 'another one']})),
      api.post_process(Filter('analyze', '$result')),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'use_v8_patch_on_chromium_trybot',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
          git_repo='https://chromium.googlesource.com/v8/v8',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
  )

  yield api.test(
      'chromium_trybot_gerrit_feature_branch',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group', builder='fake-try-builder'),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.chromium_tests.read_targets_spec('fake-group', {
          'fake-builder': {
              'gtest_tests': [{
                  'test': 'base_unittests',
              }],
          },
      }),
      api.step_data('compile (with patch)', retcode=1),
      api.tryserver.gerrit_change_target_ref('refs/heads/experimental/feature'),
      api.post_process(Filter('gerrit fetch current CL info', 'bot_update')),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'use_webrtc_patch_on_chromium_trybot',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
          git_repo='https://webrtc.googlesource.com/src'),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
  )

  yield api.test(
      'use_webrtc_patch_on_chromium_trybot_compile_failure',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
          git_repo='https://webrtc.googlesource.com/src'),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.chromium_tests.read_targets_spec('fake-group', {
          'fake-builder': {
              'gtest_tests': [{
                  'test': 'base_unittests',
              }],
          },
      }),
      api.step_data('compile (with patch)', retcode=1),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'use_skia_patch_on_chromium_trybot',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
          git_repo='https://skia.googlesource.com/skia'),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
  )

  def swarmed_webkit_tests():
    return sum([
        api.platform('linux', 64),
        api.chromium.try_build(
            builder_group='fake-try-group', builder='fake-try-builder'),
        ctbc_api.properties(ctbc_api.properties_assembler_for_try_builder()
                            .with_mirrored_builder(
                                builder_group='fake-group',
                                builder='fake-builder',
                            ).assemble()),
        swarm_hashes(extra_swarmed_tests=['blink_web_tests']),
        api.chromium_tests.read_targets_spec(
            'fake-group', {
                'fake-builder': {
                    'isolated_scripts': [{
                        'test': 'blink_web_tests',
                        'name': 'blink_web_tests',
                        'resultdb': {
                            'enable': True
                        },
                        'swarming': {
                            'dimensions': {
                                'os': 'Linux',
                            },
                        },
                        'results_handler': 'layout tests',
                    },],
                },
            }),
    ], api.empty_test_data())

  # This tests what happens if something goes horribly wrong in
  # run_web_tests.py and we return an internal error; the step should
  # be considered a hard failure and we shouldn't try to compare the
  # lists of failing tests.
  # 255 == test_run_results.UNEXPECTED_ERROR_EXIT_STATUS in run_web_tests.py.
  yield api.test(
      'swarmed_webkit_tests_unexpected_error',
      swarmed_webkit_tests(),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'blink_web_tests', 'with patch', failures=['Test.One']),
  )

  # This tests what happens if we don't trip the thresholds listed
  # above, but fail more tests than we can safely fit in a return code.
  # (this should be a soft failure and we can still retry w/o the patch
  # and compare the lists of failing tests).
  yield api.test(
      'swarmed_layout_tests_too_many_failures_for_retcode',
      swarmed_webkit_tests(),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'blink_web_tests', 'with patch', failures=['Test.One']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'blink_web_tests', 'retry shards with patch', failures=['Test.One']),
      api.expect_status('FAILURE'),
  )

  def check_ordering(check, step_odict):
    test_steps = [
        'test_pre_run (with patch).[trigger] 10_gtest (with patch)',
        'test_pre_run (with patch).[trigger] 6_isolated_tests (with patch)',
        'test_pre_run (with patch).[trigger] 5_gtest (with patch)',
        'test_pre_run (with patch).[trigger] 3_isolated_tests (with patch)',
        'test_pre_run (with patch).[trigger] 2_isolated_tests (with patch)',
        'test_pre_run (with patch).[trigger] 1_gtest (with patch)',
    ]

    for step_name in step_odict:
      if not test_steps:
        break
      if test_steps[0] == step_name:
        test_steps.pop(0)

    check(not test_steps)


  # This test is used to confirm the order of test_pre_run.
  # Test having larger shards should be triggered faster than test with smaller
  # shards.
  yield api.test(
      'sorted',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group', builder='fake-try-builder'),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      swarm_hashes(extra_swarmed_tests=[
          '1_gtest', '2_isolated_tests', '3_isolated_tests', '5_gtest',
          '6_isolated_tests', '10_gtest'
      ]),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [
                      {
                          'name': '10_gtest',
                          'swarming': {
                              'dimensions': {
                                  'os': 'Linux',
                              },
                              'shards': 10,
                          },
                      },
                      {
                          'name': '1_gtest',
                          'swarming': {
                              'dimensions': {
                                  'os': 'Linux',
                              },
                              'shards': 1,
                          },
                      },
                      {
                          'name': '5_gtest',
                          'swarming': {
                              'dimensions': {
                                  'os': 'Linux',
                              },
                              'shards': 5,
                          },
                      },
                  ],
                  'isolated_scripts': [
                      {
                          'name': '3_isolated_tests',
                          'swarming': {
                              'dimensions': {
                                  'os': 'Linux',
                              },
                              'shards': 3,
                          },
                      },
                      {
                          'name': '2_isolated_tests',
                          'swarming': {
                              'dimensions': {
                                  'os': 'Linux',
                              },
                              'shards': 2,
                          },
                      },
                      {
                          'name': '6_isolated_tests',
                          'swarming': {
                              'dimensions': {
                                  'os': 'Linux',
                              },
                              'shards': 6,
                          },
                      },
                  ],
              },
          }),
      api.post_process(check_ordering),
      api.post_process(DropExpectation),
  )
