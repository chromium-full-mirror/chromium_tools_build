# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

from PB.go.chromium.org.luci.resultdb.proto.v1 import (test_result as
                                                       test_result_pb2)

from RECIPE_MODULES.build.chromium import BuilderId
from RECIPE_MODULES.build.chromium_tests_builder_config import (
    builder_config as builder_config_module, builder_db, builder_spec)
from RECIPE_MODULES.build.chromium_tests import steps
from RECIPE_MODULES.build.test_utils.util import (RDBPerSuiteResults,
                                                  RDBPerIndividualTestResults)

DEPS = [
    'chromium',
    'chromium_tests',
    'chromium_tests_builder_config',
    'code_coverage',
    'pgo',
    'profiles',
    'recipe_engine/buildbucket',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/step',
]


def RunSteps(api):
  use_lacros = api.properties.get('use_lacros', False)
  if use_lacros:
    builders = builder_db.BuilderDatabase.create({
        'chrome.pgo': {
            'lacros-eve-pgo':
                builder_spec.BuilderSpec.create(
                    gclient_config="chromium",
                    gclient_apply_config=["chromeos"],
                    chromium_config="chromium",
                    chromium_config_kwargs={
                        'TARGET_PLATFORM': 'chromeos',
                        'TARGET_CROS_BOARDS': 'amd64-generic:eve'
                    })
        },
    })
    builder_id = BuilderId.create_for_group('chrome.pgo', 'lacros-eve-pgo')
    builder_config = builder_config_module.BuilderConfig.create(
        builders, builder_ids=[
            builder_id,
        ])
    api.path.mock_add_paths('/b/some/random/path/llvm-profdata')
  else:
    builder_id, builder_config = api.chromium_tests_builder_config.lookup_builder(
    )

  api.chromium_tests.configure_build(builder_config)
  # Fake path.
  api.profiles.src_dir = api.path['start_dir']
  # We're forcing the root profile dir to '/', such that the file.listdir
  # call being made by ensure_profdata_files can be overridden in the test run.
  # There's no way to translate a generated Path object into a string in
  # the current GenTest structure.
  api.profiles._root_profile_dir = api.path.get('/')

  if api.properties.get('mock_merged_profdata', True):
    api.path.mock_add_paths(
        api.profiles.profile_dir().join('pgo_final_aggregate.profdata'))

  test_specs = [
      steps.SwarmingIsolatedScriptTestSpec.create('performance_test_suite'),
      steps.SwarmingIsolatedScriptTestSpec.create('different_test_suite'),
  ]

  tests = [s.get_test(api.chromium_tests) for s in test_specs]

  for test in tests:
    step = test.name
    unexpected_failing_tests = set([])
    all_tests = []
    for test_name in api.properties.get('benchmark_failures', []):
      individual_test = RDBPerIndividualTestResults.create(
          test_id=test_name,
          test_results=[
              test_result_pb2.TestResult(
                  test_id='ninja://chromium/tests:browser_tests/t1',
                  expected=False,
                  status=test_result_pb2.FAIL,
              )
          ],
          test_id_prefix='',
          invocation_id=('task-chromium-swarm.appspot.com-5e052f4430ead411'),
      )
      unexpected_failing_tests.add(individual_test)
      all_tests.append(individual_test)
    # Preprocessing for test
    test.update_rdb_results(
        '',
        RDBPerSuiteResults(test.name, '', 0, set([]), unexpected_failing_tests,
                           set([]),
                           not api.properties.get('benchmark_result', True), {},
                           all_tests, ''))
    # shard_merge already ensures the profile_subdir is generated w/ step_name
    api.code_coverage.shard_merge(
        step, test.target_name, additional_merge=getattr(test, '_merge', None))

  api.pgo.process_pgo_data(tests, builder_id)

  # coverage only
  _ = api.pgo.using_pgo


def GenTests(api):

  yield api.test(
      'merged profdata does not exist',
      api.chromium.generic_build(
          builder_group='chromium.perf', builder='mac-builder-perf'),
      api.pgo(use_pgo=True),
      api.platform('mac', 64),
      api.properties(mock_merged_profdata=False),
      api.override_step_data(
          'validate benchmark results and profile data.searching for '
          'profdata files',
          api.file.listdir([
              '/performance_test_suite/performance_test_suite.profdata',
              '/different_test_suite/different_test_suite.profdata'
          ])),
      api.post_process(
          post_process.MustRun,
          'Processing PGO .profraw data.No profdata was generated.'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'basic windows',
      api.chromium.generic_build(
          builder_group='chromium.perf', builder='win64-builder-perf'),
      api.pgo(use_pgo=True),
      api.platform('win', 64),
      api.properties(mock_merged_profdata=True),
      api.override_step_data(
          'validate benchmark results and profile data.searching for '
          'profdata files',
          api.file.listdir([
              '\\\\performance_test_suite\\\\performance_test_suite.profdata',
              '\\\\different_test_suite\\\\different_test_suite.profdata'
          ])),
      api.post_process(post_process.MustRunRE, 'ensure profile dir for .*'),
      api.post_process(
          post_process.MustRun,
          'Processing PGO .profraw data.gsutil upload artifact to GS'),
      api.post_process(
          post_process.MustRun,
          'Processing PGO .profraw data.Rename the profdata artifact'),
      api.post_process(post_process.MustRun,
                       'Processing PGO .profraw data.git show'),
      api.post_process(
          post_process.MustRun,
          'Processing PGO .profraw data.merge all profile files into a single'
          ' .profdata'),
      api.post_process(
          post_process.MustRun,
          'Processing PGO .profraw data.Finding profile merge errors'),
      api.post_process(
          post_process.StepCommandContains,
          'Processing PGO .profraw data.gsutil upload artifact to GS', [
              'gs://chromium-optimization-profiles/pgo_profiles/'
              'chrome-win64-main-1587876258-'
              'ade24b3118b1feaa04cb4406253403f3f72a7f0e.profdata'
          ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'basic_android',
      api.chromium.generic_build(
          builder_group='chromium.perf', builder='android-builder-perf'),
      api.pgo(use_pgo=True),
      api.platform('linux', 32),
      api.properties(mock_merged_profdata=True),
      api.override_step_data(
          'validate benchmark results and profile data.searching for '
          'profdata files',
          api.file.listdir([
              '/performance_test_suite/performance_test_suite.profdata',
              '/different_test_suite/different_test_suite.profdata'
          ])),
      api.post_process(
          post_process.StepCommandContains,
          'Processing PGO .profraw data.gsutil upload artifact to GS', [
              'gs://chromium-optimization-profiles/pgo_profiles/'
              'chrome-android32-main-1587876258-'
              'ade24b3118b1feaa04cb4406253403f3f72a7f0e.profdata'
          ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'basic_mac_arm',
      api.chromium.generic_build(
          builder_group='chromium.perf', builder='mac-arm-builder-perf'),
      api.pgo(use_pgo=True),
      api.platform('mac', 64, arch='arm'),
      api.properties(mock_merged_profdata=True),
      api.override_step_data(
          'validate benchmark results and profile data.searching for '
          'profdata files',
          api.file.listdir([
              '/performance_test_suite/performance_test_suite.profdata',
              '/different_test_suite/different_test_suite.profdata'
          ])),
      api.post_process(
          post_process.StepCommandContains,
          'Processing PGO .profraw data.gsutil upload artifact to GS', [
              'gs://chromium-optimization-profiles/pgo_profiles/'
              'chrome-mac-arm-main-1587876258-'
              'ade24b3118b1feaa04cb4406253403f3f72a7f0e.profdata'
          ]),
      api.post_process(post_process.DropExpectation),
  )

  gn_args = '\n'.join(('cros_target_cxx = \"/b/some/random/path/llvm-nm\"',))

  yield api.test(
      'basic_lacros',
      api.chromium.generic_build(
          builder_group='chrome.pgo', builder='lacros-eve-pgo'),
      api.pgo(use_pgo=True),
      api.platform('linux', 64, arch='arm'),
      api.properties(mock_merged_profdata=True, use_lacros=True),
      api.step_data(
          'searching cros llvm toolchain.lookup GN args',
          stdout=api.raw_io.output_text(gn_args)),
      api.override_step_data(
          'validate benchmark results and profile data.searching for '
          'profdata files',
          api.file.listdir([
              '/performance_test_suite/performance_test_suite.profdata',
              '/different_test_suite/different_test_suite.profdata'
          ])),
      api.post_process(
          post_process.StepCommandContains,
          'Processing PGO .profraw data.gsutil upload artifact to GS', [
              'gs://chromium-optimization-profiles/pgo_profiles/'
              'chrome-chromeos-amd64-generic-main-1587876258-'
              'ade24b3118b1feaa04cb4406253403f3f72a7f0e.profdata'
          ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'merge errors',
      api.chromium.generic_build(
          builder_group='chromium.perf', builder='mac-builder-perf'),
      api.pgo(use_pgo=True),
      api.platform('mac', 64),
      api.properties(mock_merged_profdata=True),
      api.override_step_data(
          'validate benchmark results and profile data.searching for '
          'profdata files',
          api.file.listdir([
              '/performance_test_suite/performance_test_suite.profdata',
              '/different_test_suite/different_test_suite.profdata'
          ])),
      api.override_step_data(
          'Processing PGO .profraw data.Finding profile merge errors',
          stdout=api.json.output({
              "failed profiles": {
                  "browser_tests": ["/tmp/1/default-123.profraw"]
              },
              "total": 1
          })),
      api.post_process(
          post_process.MustRun,
          'Processing PGO .profraw data.Failing due to merge errors found '
          'alongside invalid profile data.'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'missing profdata file',
      api.chromium.generic_build(
          builder_group='chromium.perf', builder='win64-builder-perf'),
      api.pgo(use_pgo=True),
      api.platform('win', 64),
      api.properties(mock_merged_profdata=False),
      api.post_process(
          post_process.MustRun, 'validate benchmark results and profile data.'
          'searching for profdata files'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'one test missing profdata file',
      api.chromium.generic_build(
          builder_group='chromium.perf', builder='win64-builder-perf'),
      api.pgo(use_pgo=True),
      api.platform('win', 64),
      api.properties(mock_merged_profdata=False),
      api.override_step_data(
          'validate benchmark results and profile data.searching for '
          'profdata files',
          api.file.listdir(
              ['/performance_test_suite/performance_test_suite.profdata'])),
      api.post_process(
          post_process.MustRun, 'validate benchmark results and profile data.'
          'searching for profdata files'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'invalid benchmark test',
      api.chromium.generic_build(
          builder_group='chromium.perf', builder='win64-builder-perf'),
      api.pgo(use_pgo=True),
      api.platform('win', 64),
      api.properties(mock_merged_profdata=False, benchmark_result=False),
      api.override_step_data(
          'validate benchmark results and profile data.searching for '
          'profdata files',
          api.file.listdir(
              ['/performance_test_suite/performance_test_suite.profdata'])),
      api.post_process(
          post_process.MustRun, 'validate benchmark results and profile data.'
          'searching for profdata files'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'failed benchmark test',
      api.chromium.generic_build(
          builder_group='chromium.perf', builder='win64-builder-perf'),
      api.pgo(use_pgo=True),
      api.platform('win', 64),
      api.properties(mock_merged_profdata=False, benchmark_failures=['test1']),
      api.override_step_data(
          'validate benchmark results and profile data.searching for '
          'profdata files',
          api.file.listdir(
              ['/performance_test_suite/performance_test_suite.profdata'])),
      api.post_process(
          post_process.MustRun, 'validate benchmark results and profile data.'
          'searching for profdata files'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'trybot',
      api.chromium.generic_build(
          builder_group='chromium.perf', builder='win64-builder-perf'),
      api.pgo(use_pgo=True, skip_profile_upload=True),
      api.platform('win', 64),
      api.properties(mock_merged_profdata=True),
      api.override_step_data(
          'validate benchmark results and profile data.searching for '
          'profdata files',
          api.file.listdir([
              '\\\\performance_test_suite\\\\performance_test_suite.profdata',
              '\\\\different_test_suite\\\\different_test_suite.profdata'
          ])),
      api.post_process(post_process.DropExpectation),
  ) + api.post_process(post_process.DoesNotRunRE,
                       '.*gsutil upload artifact to GS.*')
