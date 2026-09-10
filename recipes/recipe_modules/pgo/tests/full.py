# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from PB.go.chromium.org.luci.resultdb.proto.v1 import common as common_pb2
from PB.go.chromium.org.luci.resultdb.proto.v1 import (
  test_result as test_result_pb2,
)

from RECIPE_MODULES.build.chromium_types import BuilderId
from RECIPE_MODULES.build.chromium_tests_builder_config import (
  builder_config as builder_config_module,
  builder_db,
  builder_spec,
)
from RECIPE_MODULES.build.chromium_tests import steps
from RECIPE_MODULES.build.test_utils.util import (
  RDBPerSuiteResults,
  RDBPerIndividualTestResults,
)

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
  chromium,
  chromium_tests,
  chromium_tests_builder_config,
  code_coverage,
  pgo,
  profiles,
)
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  file,
  json,
  path,
  platform,
  properties,
  raw_io,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  chromium: chromium.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  code_coverage: code_coverage.API
  file: file.API
  json: json.API
  path: path.API
  pgo: pgo.API
  platform: platform.API
  profiles: profiles.API
  properties: properties.API
  raw_io: raw_io.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API
  file: file.TEST_API
  json: json.TEST_API
  pgo: pgo.TEST_API
  platform: platform.TEST_API
  properties: properties.TEST_API
  raw_io: raw_io.TEST_API


def RunSteps(api: DEPS):
  source_dir = api.path.cache_dir / 'builder/src'

  use_lacros = api.properties.get('use_lacros', False)
  use_mac_arm = api.properties.get('use_mac_arm', False)
  if use_lacros:
    builders = builder_db.BuilderDatabase.create(
      {
        'chrome.pgo': {
          'lacros-eve-pgo': builder_spec.BuilderSpec.create(
            gclient_config="chromium",
            gclient_apply_config=["chromeos"],
            chromium_config="chromium",
            chromium_config_kwargs={
              'TARGET_PLATFORM': 'chromeos',
              'TARGET_CROS_BOARDS': 'amd64-generic:eve',
            },
          )
        },
      }
    )
    builder_id = BuilderId.create_for_group('chrome.pgo', 'lacros-eve-pgo')
    builder_config = builder_config_module.BuilderConfig.create(
      builders,
      builder_ids=[
        builder_id,
      ],
    )
    api.path.mock_add_paths('/b/some/random/path/llvm-profdata')
  elif use_mac_arm:
    builders = builder_db.BuilderDatabase.create(
      {
        'chrome.pgo': {
          'mac-arm-pgo': builder_spec.BuilderSpec.create(
            gclient_config="chromium",
            gclient_apply_config=["chromeos"],
            chromium_config="chromium",
            chromium_config_kwargs={
              'TARGET_PLATFORM': 'mac',
            },
          )
        },
      }
    )
    builder_id = BuilderId.create_for_group('chrome.pgo', 'mac-arm-pgo')
    builder_config = builder_config_module.BuilderConfig.create(
      builders,
      builder_ids=[
        builder_id,
      ],
    )
    api.path.mock_add_paths('/b/some/random/path/llvm-profdata')
  else:
    builder_id, builder_config = (
      api.chromium_tests_builder_config.lookup_builder()
    )

  api.chromium_tests.configure_build(builder_config)
  api.pgo.configure_llvm_tooling_path(
    source_dir, builder_id, is_cros=use_lacros
  )

  # Fake path.
  api.profiles.source_dir = api.path.start_dir

  if api.properties.get('mock_merged_profdata', True):
    api.path.mock_add_paths(
      api.profiles.profile_dir().joinpath('pgo_final_aggregate.profdata')
    )

  test_specs = []
  for name in ['performance_test_suite', 'different_test_suite']:
    spec = steps.MockTestSpec.create(
      name=name,
      per_suffix_valid={
        '': api.properties.get('benchmark_result', True),
      },
      per_suffix_failures={
        '': api.properties.get('benchmark_failures', []),
      },
    )
    test_specs.append(spec)
  tests = [s.get_test(api.chromium_tests) for s in test_specs]

  for test in tests:
    # This is needed so that ensure_profdata_files iterates over suffixes.
    test.update_rdb_results(
      '',
      RDBPerSuiteResults(
        test.name,
        common_pb2.Variant(),
        '',
        0,
        set(),
        set(),
        set(),
        False,
        {},
        [],
        '',
      ),
    )
    # shard_merge already ensures the profile_subdir is generated w/ step_name
    api.code_coverage.shard_merge(
      test.name,
      test.target_name,
      additional_merge=getattr(test, '_merge', None),
    )

  api.pgo.process_pgo_data(source_dir, tests)

  # coverage only
  _ = api.pgo.using_pgo
  _ = api.pgo.last_uploaded_pgo_filename


def GenTests(api: TEST_DEPS):

  yield api.test(
    'merged profdata does not exist',
    api.chromium.ci_build(
      builder_group='chromium.perf', builder='mac-builder-perf'
    ),
    api.pgo(use_pgo=True),
    api.platform('mac', 64),
    api.properties(mock_merged_profdata=False),
    api.override_step_data(
      'validate benchmark results and profile data.searching for '
      'profdata files',
      api.file.listdir(
        [
          'performance_test_suite/performance_test_suite.profdata',
          'different_test_suite/different_test_suite.profdata',
        ]
      ),
    ),
    api.post_process(
      post_process.MustRun,
      'Processing PGO .profraw data.No profdata was generated.',
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'basic windows',
    api.chromium.ci_build(
      builder_group='chromium.perf', builder='win64-builder-perf'
    ),
    api.pgo(use_pgo=True),
    api.platform('win', 64),
    api.properties(mock_merged_profdata=True),
    api.override_step_data(
      'validate benchmark results and profile data.searching for '
      'profdata files',
      api.file.listdir(
        [
          'performance_test_suite\\\\performance_test_suite.profdata',
          'different_test_suite\\\\different_test_suite.profdata',
        ]
      ),
    ),
    api.post_process(post_process.MustRunRE, 'ensure profile dir for .*'),
    api.post_process(
      post_process.MustRun,
      'Processing PGO .profraw data.gsutil upload artifact to GS',
    ),
    api.post_process(
      post_process.MustRun,
      'Processing PGO .profraw data.Rename the profdata artifact',
    ),
    api.post_process(
      post_process.MustRun, 'Processing PGO .profraw data.git show'
    ),
    api.post_process(
      post_process.MustRun,
      'Processing PGO .profraw data.merge all profile files into a single'
      ' .profdata',
    ),
    api.post_process(
      post_process.MustRun,
      'Processing PGO .profraw data.Finding profile merge errors',
    ),
    api.post_process(
      post_process.StepCommandContains,
      'Processing PGO .profraw data.gsutil upload artifact to GS',
      [
        'gs://chromium-optimization-profiles/pgo_profiles/'
        'chrome-win64-main-1587876258-'
        'ade24b3118b1feaa04cb4406253403f3f72a7f0e-'
        'abcdeabcdeabcdeabcdeabcdeabcdeabcdeabcde.profdata'
      ],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'basic windows arm64',
    api.chromium.ci_build(
      builder_group='chromium.perf', builder='win64-builder-perf'
    ),
    api.pgo(use_pgo=True),
    api.platform('win', 64, arch='arm'),
    api.properties(mock_merged_profdata=True),
    api.override_step_data(
      'validate benchmark results and profile data.searching for '
      'profdata files',
      api.file.listdir(
        [
          'performance_test_suite\\\\performance_test_suite.profdata',
          'different_test_suite\\\\different_test_suite.profdata',
        ]
      ),
    ),
    api.post_process(post_process.MustRunRE, 'ensure profile dir for .*'),
    api.post_process(
      post_process.MustRun,
      'Processing PGO .profraw data.gsutil upload artifact to GS',
    ),
    api.post_process(
      post_process.MustRun,
      'Processing PGO .profraw data.Rename the profdata artifact',
    ),
    api.post_process(
      post_process.MustRun, 'Processing PGO .profraw data.git show'
    ),
    api.post_process(
      post_process.MustRun,
      'Processing PGO .profraw data.merge all profile files into a single'
      ' .profdata',
    ),
    api.post_process(
      post_process.MustRun,
      'Processing PGO .profraw data.Finding profile merge errors',
    ),
    api.post_process(
      post_process.StepCommandContains,
      'Processing PGO .profraw data.gsutil upload artifact to GS',
      [
        'gs://chromium-optimization-profiles/pgo_profiles/'
        'chrome-win-arm64-main-1587876258-'
        'ade24b3118b1feaa04cb4406253403f3f72a7f0e-'
        'abcdeabcdeabcdeabcdeabcdeabcdeabcdeabcde.profdata'
      ],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'basic_android',
    api.chromium.ci_build(
      builder_group='chromium.perf', builder='android-builder-perf'
    ),
    api.pgo(use_pgo=True),
    api.platform('linux', 32),
    api.properties(mock_merged_profdata=True),
    api.override_step_data(
      'validate benchmark results and profile data.searching for '
      'profdata files',
      api.file.listdir(
        [
          'performance_test_suite/performance_test_suite.profdata',
          'different_test_suite/different_test_suite.profdata',
        ]
      ),
    ),
    api.post_process(
      post_process.StepCommandContains,
      'Processing PGO .profraw data.gsutil upload artifact to GS',
      [
        'gs://chromium-optimization-profiles/pgo_profiles/'
        'chrome-android32-main-1587876258-'
        'ade24b3118b1feaa04cb4406253403f3f72a7f0e-'
        'abcdeabcdeabcdeabcdeabcdeabcdeabcdeabcde.profdata'
      ],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'missing_refs',
    api.chromium.generic_build(
      builder_group='chromium.perf', builder='android-builder-perf'
    ),
    api.pgo(use_pgo=True),
    api.platform('linux', 32),
    api.properties(mock_merged_profdata=True),
    api.override_step_data(
      'validate benchmark results and profile data.searching for '
      'profdata files',
      api.file.listdir(
        [
          'performance_test_suite/performance_test_suite.profdata',
          'different_test_suite/different_test_suite.profdata',
        ]
      ),
    ),
    api.post_process(post_process.SummaryMarkdownRE, 'Missing ref.*'),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'override_profdata_platform',
    api.chromium.ci_build(
      builder_group='chromium.perf', builder='android-builder-perf'
    ),
    api.pgo(use_pgo=True, profdata_platform_override='android-desktop-x64'),
    api.platform('linux', 64),
    api.properties(mock_merged_profdata=True),
    api.override_step_data(
      'validate benchmark results and profile data.searching for '
      'profdata files',
      api.file.listdir(
        [
          'performance_test_suite/performance_test_suite.profdata',
          'different_test_suite/different_test_suite.profdata',
        ]
      ),
    ),
    api.post_process(
      post_process.StepCommandContains,
      'Processing PGO .profraw data.gsutil upload artifact to GS',
      [
        'gs://chromium-optimization-profiles/pgo_profiles/'
        'chrome-android-desktop-x64-main-1587876258-'
        'ade24b3118b1feaa04cb4406253403f3f72a7f0e-'
        'abcdeabcdeabcdeabcdeabcdeabcdeabcdeabcde.profdata'
      ],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'basic_mac_arm',
    api.chromium.ci_build(
      builder_group='chromium.perf', builder='mac-arm-builder-perf'
    ),
    api.pgo(use_pgo=True),
    api.platform('mac', 64, arch='arm'),
    api.properties(mock_merged_profdata=True),
    api.override_step_data(
      'validate benchmark results and profile data.searching for '
      'profdata files',
      api.file.listdir(
        [
          'performance_test_suite/performance_test_suite.profdata',
          'different_test_suite/different_test_suite.profdata',
        ]
      ),
    ),
    api.post_process(
      post_process.StepCommandContains,
      'Processing PGO .profraw data.gsutil upload artifact to GS',
      [
        'gs://chromium-optimization-profiles/pgo_profiles/'
        'chrome-mac-arm-main-1587876258-'
        'ade24b3118b1feaa04cb4406253403f3f72a7f0e-'
        'abcdeabcdeabcdeabcdeabcdeabcdeabcdeabcde.profdata'
      ],
    ),
    api.post_process(post_process.DropExpectation),
  )

  gn_args = '\n'.join(('cros_target_cxx = "/b/some/random/path/llvm-nm"',))

  yield api.test(
    'basic_lacros',
    api.chromium.ci_build(builder_group='chrome.pgo', builder='lacros-eve-pgo'),
    api.pgo(use_pgo=True),
    api.platform('linux', 64, arch='arm'),
    api.properties(mock_merged_profdata=True, use_lacros=True),
    api.step_data(
      'searching cros llvm toolchain.lookup GN args',
      stdout=api.raw_io.output_text(gn_args),
    ),
    api.override_step_data(
      'validate benchmark results and profile data.searching for '
      'profdata files',
      api.file.listdir(
        [
          'performance_test_suite/performance_test_suite.profdata',
          'different_test_suite/different_test_suite.profdata',
        ]
      ),
    ),
    api.post_process(
      post_process.StepCommandContains,
      (
        'Processing PGO .profraw data.'
        'merge all profile files into a single .profdata'
      ),
      ['--llvm-profdata', '/b/some/random/path/llvm-profdata'],
    ),
    api.post_process(
      post_process.StepCommandContains,
      'Processing PGO .profraw data.gsutil upload artifact to GS',
      [
        'gs://chromium-optimization-profiles/pgo_profiles/'
        'chrome-chromeos-amd64-generic-main-1587876258-'
        'ade24b3118b1feaa04cb4406253403f3f72a7f0e-'
        'abcdeabcdeabcdeabcdeabcdeabcdeabcdeabcde.profdata'
      ],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'merge errors',
    api.chromium.ci_build(
      builder_group='chromium.perf', builder='mac-builder-perf'
    ),
    api.pgo(use_pgo=True),
    api.platform('mac', 64),
    api.properties(mock_merged_profdata=True),
    api.override_step_data(
      'validate benchmark results and profile data.searching for '
      'profdata files',
      api.file.listdir(
        [
          'performance_test_suite/performance_test_suite.profdata',
          'different_test_suite/different_test_suite.profdata',
        ]
      ),
    ),
    api.override_step_data(
      'Processing PGO .profraw data.Finding profile merge errors',
      stdout=api.json.output(
        {
          "failed profiles": {"browser_tests": ["/tmp/1/default-123.profraw"]},
          "total": 1,
        }
      ),
    ),
    api.post_process(
      post_process.MustRun,
      'Processing PGO .profraw data.Failing due to merge errors found '
      'alongside invalid profile data.',
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'missing profdata file',
    api.chromium.ci_build(
      builder_group='chromium.perf', builder='win64-builder-perf'
    ),
    api.pgo(use_pgo=True),
    api.platform('win', 64),
    api.properties(mock_merged_profdata=False),
    api.post_process(
      post_process.MustRun,
      'validate benchmark results and profile data.'
      'searching for profdata files',
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'one test missing profdata file',
    api.chromium.ci_build(
      builder_group='chromium.perf', builder='win64-builder-perf'
    ),
    api.pgo(use_pgo=True),
    api.platform('win', 64),
    api.properties(mock_merged_profdata=False),
    api.override_step_data(
      'validate benchmark results and profile data.searching for '
      'profdata files',
      api.file.listdir(
        ['performance_test_suite/performance_test_suite.profdata']
      ),
    ),
    api.post_process(
      post_process.MustRun,
      'validate benchmark results and profile data.'
      'searching for profdata files',
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'invalid benchmark test',
    api.chromium.ci_build(
      builder_group='chromium.perf', builder='win64-builder-perf'
    ),
    api.pgo(use_pgo=True),
    api.platform('win', 64),
    api.properties(mock_merged_profdata=False, benchmark_result=False),
    api.override_step_data(
      'validate benchmark results and profile data.searching for '
      'profdata files',
      api.file.listdir(
        ['performance_test_suite/performance_test_suite.profdata']
      ),
    ),
    api.post_process(
      post_process.MustRun,
      'validate benchmark results and profile data.'
      'searching for profdata files',
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'failed benchmark test',
    api.chromium.ci_build(
      builder_group='chromium.perf', builder='win64-builder-perf'
    ),
    api.pgo(use_pgo=True),
    api.platform('win', 64),
    api.properties(mock_merged_profdata=False, benchmark_failures=['test1']),
    api.override_step_data(
      'validate benchmark results and profile data.searching for '
      'profdata files',
      api.file.listdir(
        ['performance_test_suite/performance_test_suite.profdata']
      ),
    ),
    api.post_process(
      post_process.MustRun,
      'validate benchmark results and profile data.'
      'searching for profdata files',
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'trybot',
    api.chromium.ci_build(
      builder_group='chromium.perf', builder='win64-builder-perf'
    ),
    api.pgo(use_pgo=True, skip_profile_upload=True),
    api.platform('win', 64),
    api.properties(mock_merged_profdata=True),
    api.override_step_data(
      'validate benchmark results and profile data.searching for '
      'profdata files',
      api.file.listdir(
        [
          'performance_test_suite\\\\performance_test_suite.profdata',
          'different_test_suite\\\\different_test_suite.profdata',
        ]
      ),
    ),
    api.post_process(post_process.DropExpectation),
  ) + api.post_process(
    post_process.DoesNotRunRE, '.*gsutil upload artifact to GS.*'
  )

  yield api.test(
    'weights',
    api.chromium.ci_build(builder_group='chrome.pgo', builder='mac-arm-pgo'),
    api.pgo(use_pgo=True),
    api.platform('mac', 64, arch='arm'),
    api.properties(mock_merged_profdata=True, use_mac_arm=True),
    api.override_step_data(
      'validate benchmark results and profile data.searching for '
      'profdata files',
      api.file.listdir(
        [
          'performance_test_suite/performance_test_suite.profdata',
          'different_test_suite/different_test_suite.profdata',
        ]
      ),
    ),
    api.post_process(
      post_process.StepCommandContains,
      'Processing PGO .profraw data.gsutil upload artifact to GS',
      [
        'gs://chromium-optimization-profiles/pgo_profiles/'
        'chrome-mac-arm-main-1587876258-'
        'ade24b3118b1feaa04cb4406253403f3f72a7f0e-'
        'abcdeabcdeabcdeabcdeabcdeabcdeabcdeabcde.profdata'
      ],
    ),
    api.post_process(post_process.DropExpectation),
  )
