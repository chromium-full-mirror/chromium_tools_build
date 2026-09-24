# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process
from recipe_engine.recipe_api import Property

from PB.recipe_engine import result as result_pb2
from PB.recipe_modules.build.archive import properties as archive_properties
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb2

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc
from RECIPE_MODULES.build.chromium_tests import steps

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
  chromium,
  chromium_swarming,
  chromium_tests,
  chromium_tests_builder_config,
  code_coverage,
  orderfile,
  pgo,
  pinlist,
  profiles,
  test_utils,
)
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  file,
  json,
  legacy_annotation,
  path,
  platform,
  properties,
  raw_io,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  chromium: chromium.API
  chromium_swarming: chromium_swarming.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  code_coverage: code_coverage.API
  file: file.API
  json: json.API
  legacy_annotation: legacy_annotation.API
  orderfile: orderfile.API
  path: path.API
  pgo: pgo.API
  pinlist: pinlist.API
  platform: platform.API
  profiles: profiles.API
  properties: properties.API
  raw_io: raw_io.API
  test_utils: test_utils.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API
  chromium_swarming: chromium_swarming.TEST_API
  chromium_tests: chromium_tests.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  code_coverage: code_coverage.TEST_API
  file: file.TEST_API
  json: json.TEST_API
  orderfile: orderfile.TEST_API
  path: path.TEST_API
  pgo: pgo.TEST_API
  pinlist: pinlist.TEST_API
  platform: platform.TEST_API
  properties: properties.TEST_API
  raw_io: raw_io.TEST_API
  test_utils: test_utils.TEST_API


PROPERTIES = {'fail_compile': Property(default=False, kind=bool)}


def _long_test_name():
  return 'a' * 701


def _builder_spec(**kwargs):
  return ctbc.BuilderSpec.create(**kwargs)


CUSTOM_BUILDERS = ctbc.BuilderDatabase.create(
  {
    'chromium.example': {
      'Isolated Transfer Builder': _builder_spec(
        chromium_apply_config=['mb'],
        chromium_config='chromium',
        chromium_config_kwargs={
          'BUILD_CONFIG': 'Release',
          'TARGET_BITS': 64,
        },
        gclient_config='chromium',
        simulation_platform='linux',
      ),
      'Isolated Transfer Tester': _builder_spec(
        execution_mode=ctbc.TEST,
        chromium_apply_config=['mb'],
        chromium_config='chromium',
        chromium_config_kwargs={
          'BUILD_CONFIG': 'Release',
          'TARGET_BITS': 64,
        },
        gclient_config='chromium',
        parent_buildername='Isolated Transfer Builder',
        simulation_platform='linux',
      ),
      'Isolated Transfer: mixed builder, isolated tester (builder)': _builder_spec(
        chromium_apply_config=['mb'],
        chromium_config='chromium',
        chromium_config_kwargs={
          'BUILD_CONFIG': 'Release',
          'TARGET_BITS': 64,
        },
        gclient_config='chromium',
        simulation_platform='linux',
      ),
      'Isolated Transfer: mixed builder, isolated tester (tester)': _builder_spec(
        execution_mode=ctbc.TEST,
        chromium_apply_config=['mb'],
        chromium_config='chromium',
        chromium_config_kwargs={
          'BUILD_CONFIG': 'Release',
          'TARGET_BITS': 64,
        },
        gclient_config='chromium',
        parent_buildername=(
          'Isolated Transfer: mixed builder, isolated tester (builder)'
        ),
        simulation_platform='linux',
      ),
      'Isolated Transfer: mixed BT, isolated tester (BT)': _builder_spec(
        android_config='base_config',
        chromium_config='android',
        chromium_config_kwargs={
          'BUILD_CONFIG': 'Release',
          'TARGET_BITS': 64,
          'TARGET_PLATFORM': 'android',
        },
        gclient_config='chromium',
        simulation_platform='linux',
      ),
      'Isolated Transfer: mixed BT, isolated tester (tester)': _builder_spec(
        android_config='base_config',
        execution_mode=ctbc.TEST,
        chromium_config='android',
        chromium_config_kwargs={
          'BUILD_CONFIG': 'Release',
          'TARGET_BITS': 64,
          'TARGET_PLATFORM': 'android',
        },
        gclient_config='chromium',
        parent_buildername='Isolated Transfer: mixed BT, isolated tester (BT)',
        simulation_platform='linux',
      ),
      'Packaged Transfer Builder': _builder_spec(
        chromium_apply_config=['mb'],
        chromium_config='chromium',
        chromium_config_kwargs={
          'BUILD_CONFIG': 'Release',
          'TARGET_BITS': 64,
        },
        gclient_config='chromium',
        simulation_platform='linux',
      ),
      'Packaged Transfer Tester': _builder_spec(
        execution_mode=ctbc.TEST,
        chromium_apply_config=['mb'],
        chromium_config='chromium',
        chromium_config_kwargs={
          'BUILD_CONFIG': 'Release',
          'TARGET_BITS': 64,
        },
        gclient_config='chromium',
        parent_buildername='Packaged Transfer Builder',
        simulation_platform='linux',
      ),
      'Multiple Triggers: Builder': _builder_spec(
        android_config='base_config',
        chromium_apply_config=['mb'],
        chromium_config='android',
        chromium_config_kwargs={
          'BUILD_CONFIG': 'Release',
          'TARGET_BITS': 32,
          'TARGET_PLATFORM': 'android',
        },
        gclient_apply_config=['android'],
        gclient_config='chromium',
        simulation_platform='linux',
      ),
      'Multiple Triggers: Mixed': _builder_spec(
        android_config='base_config',
        execution_mode=ctbc.TEST,
        chromium_apply_config=['mb'],
        chromium_config='android',
        chromium_config_kwargs={
          'BUILD_CONFIG': 'Release',
          'TARGET_BITS': 32,
          'TARGET_PLATFORM': 'android',
        },
        gclient_apply_config=['android'],
        gclient_config='chromium',
        parent_buildername='Multiple Triggers: Builder',
        simulation_platform='linux',
      ),
      'Multiple Triggers: Isolated': _builder_spec(
        android_config='base_config',
        execution_mode=ctbc.TEST,
        chromium_apply_config=['mb'],
        chromium_config='android',
        chromium_config_kwargs={
          'BUILD_CONFIG': 'Release',
          'TARGET_BITS': 32,
          'TARGET_PLATFORM': 'android',
        },
        gclient_apply_config=['android'],
        gclient_config='chromium',
        parent_buildername='Multiple Triggers: Builder',
        simulation_platform='linux',
      ),
    },
  }
)


def NotIdempotent(check, step_odict, step):
  check(
    'Idempotent flag unexpected', '--idempotent' not in step_odict[step].cmd
  )


def RunSteps(api: DEPS, fail_compile):
  api.profiles._root_profile_dir = api.path.cache_dir / 'profile_root'
  api.path.mock_add_paths(
    api.profiles.profile_dir().joinpath('overall-merged.profdata')
  )
  api.path.mock_add_paths(
    api.profiles.profile_dir().joinpath(api.pgo.TEMP_PROFDATA_FILENAME)
  )
  api.path.mock_add_paths(api.profiles.profile_dir().joinpath('orderfile.out'))
  api.path.mock_add_paths(api.pinlist.pinlist_dir.joinpath('pinlist.meta'))
  source_dir = api.path.cache_dir / 'builder/src'
  api.path.mock_add_paths(source_dir / 'chrome/build/pgo_profiles/profile.pgo')

  # override compile_specific_targets to control compile step failure state
  def compile_override(*args, **kwargs):
    return result_pb2.RawResult(
      status=common_pb2.FAILURE, summary_markdown='Compile step failed.'
    ), None

  if fail_compile:
    api.chromium_tests.compile_specific_targets = compile_override

  builder_id, builder_config = (
    api.chromium_tests_builder_config.lookup_builder()
  )
  build_result, _ = api.chromium_tests.main_waterfall_steps(
    builder_id, builder_config
  )
  return build_result


def GenTests(api: TEST_DEPS):
  ctbc_api = api.chromium_tests_builder_config

  yield api.test(
    'builder',
    api.platform('linux', 64),
    api.chromium.ci_build(builder_group='fake-group', builder='fake-builder'),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_ci_builder(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .with_tester(
        builder_group='fake-group',
        builder='fake-tester',
      )
      .assemble()
    ),
    api.override_step_data(
      'trigger',
      stdout=api.raw_io.output_text("""
        {
          "builds":[{
           "status": "SCHEDULED",
           "created_ts": "1459200369835900",
           "bucket": "user.username",
           "result_details_json": "null",
           "status_changed_ts": "1459200369835930",
           "created_by": "user:username@example.com",
           "updated_ts": "1459200369835940",
           "utcnow_ts": "1459200369962370",
           "parameters_json": "{\\"This_has_been\\": \\"removed\\"}",
           "id": "9016911228971028736"
          }],
          "kind": "buildbucket#resourcesItem",
          "etag": "\\"8uCIh8TRuYs4vPN3iWmly9SJMqw\\""
        }
      """),
    ),
    api.post_process(
      post_process.StepSuccess, 'read test spec (fake-group.json)'
    ),
    api.post_process(
      post_process.StepCommandContains,
      'lookup GN args',
      [
        '-m',
        'fake-group',
        '-b',
        'fake-builder',
      ],
    ),
    api.post_process(post_process.StepSuccess, 'compile'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'failed_test_on_tester',
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
        builder_spec=ctbc.BuilderSpec.create(
          gclient_config='chromium',
          chromium_config='chromium',
        ),
      )
      .with_parent(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.properties(
      swarm_hashes={'base_unittests': '[dummy hash for base_unittests/size]'}
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
                  'os': 'linux',
                },
              },
            }
          ],
        },
      },
    ),
    api.chromium_tests.gen_swarming_and_rdb_results(
      'base_unittests', '', failures=['Test.One']
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DoesNotRun, 'abort retry'),
    api.post_process(post_process.MustRun, 'record test suite statuses'),
    api.post_process(
      post_process.PropertyEquals, 'test_status', {'base_unittests': 'Failure'}
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'retry_failed_shards_enabled_on_tester',
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
        builder_spec=ctbc.BuilderSpec.create(
          gclient_config='chromium',
          chromium_config='chromium',
        ),
        retry_failed_shards=True,
      )
      .with_parent(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.properties(
      swarm_hashes={
        'base_unittests': '[dummy hash for base_unittests/size]',
        _long_test_name(): '[dummy hash for base_unittests/size]',
        'base_unittests2': '[dummy hash for base_unittests/size]',
        'base_unittests3': '[dummy hash for base_unittests/size]',
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
                  'os': 'linux',
                },
              },
            },
            {
              'test': _long_test_name(),
              'swarming': {
                'dimensions': {
                  'os': 'linux',
                },
              },
            },
            {
              'test': 'base_unittests2',
              'swarming': {
                'dimensions': {
                  'os': 'linux',
                },
              },
            },
            {
              'test': 'base_unittests3',
              'swarming': {
                'dimensions': {
                  'os': 'linux',
                },
              },
            },
          ],
        },
      },
    ),
    api.chromium_tests.gen_swarming_and_rdb_results(
      'base_unittests', '', failures=['Test.One']
    ),
    api.chromium_tests.gen_swarming_and_rdb_results(
      _long_test_name(), '', failures=['Test.One']
    ),
    api.chromium_tests.gen_swarming_and_rdb_results(
      'base_unittests2', '', failures=['Test.One']
    ),
    api.expect_status('SUCCESS'),
    api.post_process(post_process.MustRun, 'base_unittests'),
    api.post_process(post_process.MustRun, 'base_unittests (retry shards)'),
    api.post_process(post_process.MustRun, _long_test_name()),
    api.post_process(
      post_process.MustRun, _long_test_name() + ' (retry shards)'
    ),
    api.post_process(post_process.MustRun, 'base_unittests2'),
    api.post_process(post_process.MustRun, 'base_unittests2 (retry shards)'),
    api.post_process(post_process.MustRun, 'base_unittests3'),
    api.post_process(post_process.DoesNotRun, 'base_unittests3 (retry shards)'),
    api.post_process(post_process.MustRun, 'record test suite statuses'),
    api.post_process(
      post_process.PropertyEquals,
      'test_status',
      {
        'base_unittests': 'Success',
        _long_test_name(): 'Success',
        'base_unittests2': 'Success',
        'base_unittests3': 'Success',
      },
    ),
    api.post_process(
      post_process.SummaryMarkdown,
      '3 Test Suite(s) succeeded after retry.\n\n- '
      + _long_test_name()
      + '\n- ...2 more failure(s)...\n',
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'retry_invalid_shards_enabled_on_tester',
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
        builder_spec=ctbc.BuilderSpec.create(
          gclient_config='chromium',
          chromium_config='chromium',
        ),
        retry_failed_shards=True,
        retry_invalid_shards=True,
      )
      .with_parent(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.properties(
      swarm_hashes={'base_unittests': '[dummy hash for base_unittests/size]'}
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
                  'os': 'linux',
                },
              },
            }
          ],
        },
      },
    ),
    api.chromium_tests.gen_swarming_and_rdb_results(
      'base_unittests', '', invalid=True
    ),
    api.expect_status('SUCCESS'),
    api.post_process(post_process.MustRun, 'base_unittests'),
    api.post_process(post_process.MustRun, 'base_unittests (retry shards)'),
    api.post_process(post_process.MustRun, 'record test suite statuses'),
    api.post_process(
      post_process.PropertyEquals, 'test_status', {'base_unittests': 'Success'}
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'success_test_on_tester',
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
        builder_spec=ctbc.BuilderSpec.create(
          gclient_config='chromium',
          chromium_config='chromium',
        ),
      )
      .with_parent(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.properties(
      swarm_hashes={'base_unittests': '[dummy hash for base_unittests/size]'}
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
                  'os': 'linux',
                },
              },
            }
          ],
        },
      },
    ),
    api.chromium_tests.gen_swarming_and_rdb_results(
      'base_unittests', '', successes=['Test.One']
    ),
    api.post_process(post_process.MustRun, 'record test suite statuses'),
    api.post_process(
      post_process.PropertyEquals, 'test_status', {'base_unittests': 'Success'}
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'mac-thin-tester',
    api.platform('linux', 64),
    api.chromium.ci_build(builder_group='fake-group', builder='fake-tester'),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_ci_tester(
        builder_group='fake-group',
        builder='fake-tester',
        builder_spec=ctbc.BuilderSpec.create(
          gclient_config='chromium',
          chromium_config='chromium',
          chromium_config_kwargs={
            'TARGET_PLATFORM': 'mac',
          },
        ),
      )
      .with_parent(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.properties(
      swarm_hashes={'base_unittests': '[dummy hash for base_unittests/size]'}
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
                  'os': 'Mac',
                },
              },
            }
          ],
        },
      },
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'ci_only_test_failure',
    api.platform('linux', 64),
    api.chromium.ci_build(builder_group='fake-group', builder='fake-builder'),
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
              'test': 'base_unittests',
              'swarming': {
                'dimensions': {
                  'os': 'linux',
                },
              },
              'ci_only': True,
            }
          ],
        },
      },
    ),
    api.chromium_tests.gen_swarming_and_rdb_results(
      'base_unittests', '', failures=['Test.One']
    ),
    api.expect_status('FAILURE'),
    api.post_process(
      post_process.SummaryMarkdownRE,
      (
        'some of the failing tests are only run in CI,'
        ' add the following CL footers to enable them on try builders\n\n'
        f'{steps.INCLUDE_CI_FOOTER}: fake-group:fake-builder|base_unittests'
      ),
    ),
    api.post_process(post_process.DropExpectation),
  )

  def check_gs_url_equals(check, steps_dict, dest, expected):
    step_name = 'Generic Archiving Steps.gsutil upload %s' % dest
    check(step_name in steps_dict)
    check(expected == steps_dict[step_name].cmd[-1])

  input_properties = archive_properties.InputProperties()
  archive_data = archive_properties.ArchiveData()
  archive_data.dirs.extend(['anydir'])
  archive_data.gcs_bucket = 'any-bucket'
  archive_data.gcs_path = 'x86/{%chrome_version%}/chrome'
  archive_data.archive_type = archive_properties.ArchiveData.ARCHIVE_TYPE_ZIP
  input_properties.archive_datas.extend([archive_data])
  yield api.test(
    'archive_builder_no_chrome_version',
    api.platform('linux', 64),
    api.chromium.ci_build(
      builder_group='fake-group',
      builder='fake-builder',
      revision='refs/tags/1.2.3.4',
      git_ref='refs/tags/1.2.3.4',
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_ci_builder(
        builder_group='fake-group',
        builder='fake-builder',
      ).assemble()
    ),
    api.properties(chrome_version=None, **{'$build/archive': input_properties}),
    api.post_process(
      check_gs_url_equals,
      'x86/1.2.3.4/chrome',
      'gs://any-bucket/x86/1.2.3.4/chrome',
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'archive_builder_with_chrome_version',
    api.platform('linux', 64),
    api.chromium.ci_build(
      builder_group='fake-group',
      builder='fake-builder',
      revision='refs/tags/1.2.3.4',
      git_ref='refs/tags/1.2.3.4',
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_ci_builder(
        builder_group='fake-group',
        builder='fake-builder',
      ).assemble()
    ),
    api.properties(
      chrome_version='2.2.2.2', **{'$build/archive': input_properties}
    ),
    api.post_process(
      check_gs_url_equals,
      'x86/2.2.2.2/chrome',
      'gs://any-bucket/x86/2.2.2.2/chrome',
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'archive_builder_branch_number_chrome_version',
    api.platform('linux', 64),
    api.chromium.ci_build(
      builder_group='fake-group',
      builder='fake-builder',
      revision='refs/tags/1.2.3.4',
      git_ref='refs/tags/1.2.3.4',
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_ci_builder(
        builder_group='fake-group',
        builder='fake-builder',
      ).assemble()
    ),
    api.properties(
      chrome_version='8064', **{'$build/archive': input_properties}
    ),
    api.post_process(
      check_gs_url_equals,
      'x86/8064/chrome',
      'gs://any-bucket/x86/8064/chrome',
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'archive_builder_invalid_chrome_version',
    api.platform('linux', 64),
    api.chromium.ci_build(
      builder_group='fake-group',
      builder='fake-builder',
      revision='refs/tags/1.2.3.4',
      git_ref='refs/tags/1.2.3.4',
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_ci_builder(
        builder_group='fake-group',
        builder='fake-builder',
      ).assemble()
    ),
    api.properties(
      chrome_version='not-a-version',
      **{'$build/archive': input_properties},
    ),
    api.post_process(post_process.MustRun, 'Invalid chrome_version'),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'code_coverage_ci_bots',
    api.chromium.generic_build(
      builder_group='fake-group', builder='fake-builder'
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_ci_builder(
        builder_group='fake-group',
        builder='fake-builder',
      ).assemble()
    ),
    api.properties(
      swarm_hashes={'base_unittests': '[dummy hash for base_unittests/size]'}
    ),
    api.code_coverage(use_clang_coverage=True),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-builder': {
          'gtest_tests': [
            {
              'isolate_profile_data': True,
              'test': 'base_unittests',
              'swarming': {
                'dimensions': {
                  'os': 'Linux',
                },
              },
            }
          ],
        },
      },
    ),
    api.override_step_data(
      'base_unittests',
      api.chromium_swarming.canned_summary_output(
        api.test_utils.canned_gtest_output(True), internal_failure=True
      ),
    ),
    api.post_process(post_process.MustRun, 'base_unittests (retry shards)'),
    api.post_process(
      post_process.MustRun,
      'process clang code coverage data for overall test coverage.'
      'generate metadata for overall test coverage in 2 tests',
    ),
    api.post_process(
      NotIdempotent,
      'test_pre_run (retry shards).[trigger] base_unittests (retry shards)',
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'orderfile_ci_bots',
    api.chromium.ci_build(builder_group='fake-group', builder='fake-builder'),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_ci_builder(
        builder_group='fake-group',
        builder='fake-builder',
        builder_spec=ctbc.BuilderSpec.create(
          gclient_config='chromium',
          chromium_config='chromium',
          chromium_config_kwargs={
            'TARGET_ARCH': 'arm',
            'TARGET_BITS': 64,
          },
        ),
      ).assemble()
    ),
    api.properties(
      swarm_hashes={
        'performance_test_suite': '[dummy hash for performance_test_suite/size]'
      },
    ),
    api.orderfile(
      use_orderfile=True,
      upload_orderfile=True,
      last_uploaded_pgo_filename='profile.pgo',
    ),
    api.platform('linux', 64),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-builder': {
          'isolated_scripts': [
            {
              'name': 'performance_test_suite',
              'isolate_profile_data': True,
              'test': 'performance_test_suite',
              'swarming': {
                'dimensions': {
                  'os': 'Linux',
                },
              },
            }
          ],
        },
      },
    ),
    api.post_process(post_process.MustRun, 'processing generated orderfile'),
    api.post_process(
      post_process.MustRun,
      'processing generated orderfile.register '
      'chromium/chrome/android/orderfiles/arm64',
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'pgo_ci_bots',
    api.chromium.ci_build(builder_group='fake-group', builder='fake-builder'),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_ci_builder(
        builder_group='fake-group',
        builder='fake-builder',
      ).assemble()
    ),
    api.properties(
      swarm_hashes={
        'performance_test_suite': '[dummy hash for performance_test_suite/size]'
      },
      xcode_build_version='11c29',
    ),
    api.pgo(use_pgo=True),
    api.platform('mac', 64),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-builder': {
          'isolated_scripts': [
            {
              'name': 'performance_test_suite',
              'isolate_profile_data': True,
              'test': 'performance_test_suite',
              'swarming': {
                'dimensions': {
                  'os': 'Linux',
                },
              },
            }
          ],
        },
      },
    ),
    api.override_step_data(
      'validate benchmark results and profile data.searching for '
      'profdata files',
      api.file.listdir(
        ['/performance_test_suite/performance_test_suite.profdata']
      ),
    ),
    api.post_process(
      post_process.MustRun,
      'Processing PGO .profraw data.merge all profile files into a single '
      '.profdata',
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'java_code_coverage_ci_bots',
    api.chromium.generic_build(
      builder_group='fake-group', builder='fake-builder'
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_ci_builder(
        builder_group='fake-group',
        builder='fake-builder',
        builder_spec=ctbc.BuilderSpec.create(
          gclient_config='chromium',
          chromium_config='chromium',
          chromium_config_kwargs={
            'TARGET_PLATFORM': 'android',
          },
          android_config='base_config',
        ),
      ).assemble()
    ),
    api.properties(
      swarm_hashes={
        'chrome_public_test_apk': '[dummy hash for chrome_public_test_apk/size]'
      }
    ),
    api.code_coverage(use_java_coverage=True),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-builder': {
          'gtest_tests': [
            {
              'isolate_profile_data': True,
              'test': 'chrome_public_test_apk',
              'swarming': {},
            }
          ],
        },
      },
    ),
    api.post_process(post_process.MustRun, 'chrome_public_test_apk on Android'),
    api.post_process(post_process.MustRun, 'process java coverage (overall)'),
    api.post_process(post_process.DropExpectation),
  )

  def TriggersBuilderWithProperties(
    check, step_odict, builder='', properties=None
  ):
    trigger_step = step_odict['trigger']
    check(
      '"trigger" step did not run expected command.',
      'scheduler.Scheduler.EmitTriggers' in trigger_step.cmd
      and trigger_step.stdin,
    )
    trigger_json = api.json.loads(trigger_step.stdin)

    for batch in trigger_json.get('batches', []):
      if any(builder == j.get('job') for j in batch.get('jobs', [])):
        actual_properties = (
          batch.get('trigger', {}).get('gitiles', {}).get('properties', {})
        )
        check(all(p in actual_properties for p in properties))
        break
    else:  # pragma: no cover
      check('"%s" not triggered' % builder, False)

  yield api.test(
    'isolate_transfer_builder',
    api.chromium_tests_builder_config.ci_build(
      builder_group='chromium.example',
      builder='Isolated Transfer Builder',
      build_number=123,
      bot_id='isolated_transfer_builder_id',
      builder_db=CUSTOM_BUILDERS,
    ),
    api.chromium_tests.read_targets_spec(
      'chromium.example',
      {
        'Isolated Transfer Tester': {
          'gtest_tests': [
            {
              'args': ['--sample-argument'],
              'swarming': {},
              'test': 'base_unittests',
            },
          ],
        },
      },
    ),
    api.post_process(post_process.DoesNotRun, 'package build'),
    api.post_process(
      TriggersBuilderWithProperties,
      builder='Isolated Transfer Tester',
      properties=['swarm_hashes'],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'isolate_transfer_tester',
    api.chromium_tests_builder_config.ci_build(
      builder_group='chromium.example',
      builder='Isolated Transfer Tester',
      parent_buildername='Isolated Transfer Builder',
      build_number=123,
      bot_id='isolated_transfer_tester_id',
      builder_db=CUSTOM_BUILDERS,
    ),
    api.properties(
      swarm_hashes={
        'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
      }
    ),
    api.chromium_tests.read_targets_spec(
      'chromium.example',
      {
        'Isolated Transfer Tester': {
          'gtest_tests': [
            {
              'args': ['--sample-argument'],
              'swarming': {
                'dimensions': {
                  'os': 'Linux',
                },
              },
              'test': 'base_unittests',
            },
          ],
        },
      },
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'isolated_transfer__mixed_builder_isolated_tester',
    api.chromium_tests_builder_config.ci_build(
      builder_group='chromium.example',
      builder=('Isolated Transfer: mixed builder, isolated tester (builder)'),
      build_number=123,
      bot_id='isolated_transfer_builder_id',
      builder_db=CUSTOM_BUILDERS,
    ),
    api.chromium_tests.read_targets_spec(
      'chromium.example',
      {
        'Isolated Transfer: mixed builder, isolated tester (builder)': {
          'scripts': [
            {
              'name': 'check_network_annotations',
              'script': 'check_network_annotations.py',
            },
          ],
        },
        'Isolated Transfer: mixed builder, isolated tester (tester)': {
          'gtest_tests': [
            {
              'args': ['--sample-argument'],
              'swarming': {},
              'test': 'base_unittests',
            },
          ],
        },
      },
    ),
    api.post_process(post_process.DoesNotRun, 'package build'),
    api.post_process(
      TriggersBuilderWithProperties,
      builder='Isolated Transfer: mixed builder, isolated tester (tester)',
      properties=['swarm_hashes'],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'compile_failure',
    api.platform('linux', 64),
    api.chromium.ci_build(builder_group='fake-group', builder='fake-builder'),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_ci_builder(
        builder_group='fake-group',
        builder='fake-builder',
      ).assemble()
    ),
    api.properties(fail_compile=True),
    api.expect_status('FAILURE'),
    api.post_process(post_process.SummaryMarkdown, 'Compile step failed.'),
    api.post_process(post_process.DropExpectation),
  )

  fake_group = 'fake-group'
  fake_builder = 'fake-builder'
  fake_test = 'fake_test'

  def PropertyExists(check, step_odict, key):
    properties = post_process.GetBuildProperties(step_odict)
    check(key in properties)

  fake_triggered_builder = fake_builder + '-tests'

  yield api.test(
    'ci_bot_expose_trigger_properties',
    api.properties(
      config='Release',
      swarm_hashes={fake_test: 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeee/size'},
    ),
    api.path.exists(
      api.path.cache_dir
      / 'builder/src/out/ceb4-fake-builder/orchestrator_all.runtime_deps'
    ),
    api.platform('linux', 64),
    api.chromium_tests_builder_config.ci_build(
      builder_group=fake_group,
      builder=fake_builder,
      builder_db=ctbc.BuilderDatabase.create(
        {
          fake_group: {
            fake_builder: ctbc.BuilderSpec.create(
              chromium_config='chromium',
              gclient_config='chromium',
              expose_trigger_properties=True,
            ),
            fake_triggered_builder: ctbc.BuilderSpec.create(
              chromium_config='chromium',
              gclient_config='chromium',
              expose_trigger_properties=True,
              parent_buildername=fake_builder,
              execution_mode=ctbc.TEST,
            ),
          }
        }
      ),
    ),
    api.chromium_tests.read_targets_spec(
      fake_group,
      {
        fake_triggered_builder: {
          'isolated_scripts': [
            {
              'name': fake_test,
              'swarming': {},
            }
          ],
        }
      },
    ),
    api.step_data(
      'archive test-trigger deps.read test-trigger deps file',
      api.file.read_text(
        '../../testing/buildbot/*.json\n'
        '../../testing/merge_scripts/merge_api.py\n'
        '../../testing/merge_scripts/standard_gtest_merge.py'
      ),
    ),
    api.post_process(post_process.MustRun, 'isolate tests'),
    api.post_process(post_process.MustRun, 'archive command lines to RBE-CAS'),
    api.post_process(PropertyExists, 'trigger_properties'),
    api.post_process(post_process.DoesNotRun, 'mark: before_tests'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'ci_bot_expose_trigger_properties_via_test_trigger_cas',
    api.properties(
      config='Release',
      swarm_hashes={fake_test: 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeee/size'},
    ),
    api.path.exists(
      api.path.cache_dir
      / 'builder/src/out/ceb4-fake-builder/orchestrator_all.runtime_deps'
    ),
    api.platform('linux', 64),
    api.chromium_tests_builder_config.ci_build(
      builder_group=fake_group,
      builder=fake_builder,
      builder_db=ctbc.BuilderDatabase.create(
        {
          fake_group: {
            fake_builder: ctbc.BuilderSpec.create(
              chromium_config='chromium',
              gclient_config='chromium',
            ),
            fake_triggered_builder: ctbc.BuilderSpec.create(
              chromium_config='chromium',
              gclient_config='chromium',
              parent_buildername=fake_builder,
              execution_mode=ctbc.TEST,
              use_test_trigger_cas=True,
            ),
          }
        }
      ),
    ),
    api.chromium_tests.read_targets_spec(
      fake_group,
      {
        fake_triggered_builder: {
          'isolated_scripts': [
            {
              'name': fake_test,
              'swarming': {},
            }
          ],
        }
      },
    ),
    api.step_data(
      'archive test-trigger deps.read test-trigger deps file',
      api.file.read_text(
        '../../testing/buildbot/*.json\n'
        '../../testing/merge_scripts/merge_api.py\n'
        '../../testing/merge_scripts/standard_gtest_merge.py'
      ),
    ),
    api.post_process(post_process.MustRun, 'isolate tests'),
    api.post_process(post_process.MustRun, 'archive command lines to RBE-CAS'),
    api.post_process(PropertyExists, 'trigger_properties'),
    api.post_check(
      lambda check, steps: check(
        'infra/orchestrator:orchestrator_all' in steps['compile'].cmd
      )
    ),
    api.post_process(post_process.DoesNotRun, 'mark: before_tests'),
    api.post_process(post_process.DropExpectation),
  )

  def LogDoesNotEqual(check, step_odict, step, log, expected):
    check(step_odict[step].logs[log] != expected)

  # Triggered testers can also be configured to expose trigger properties.
  yield api.test(
    'ci_triggered_tester_expose_trigger_properties',
    api.properties(
      config='Release',
      swarm_hashes={fake_test: 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeee/size'},
      swarming_command_lines_digest='deadbeef/20',
    ),
    api.platform('linux', 64),
    api.chromium_tests_builder_config.ci_build(
      builder_group=fake_group,
      builder=fake_triggered_builder,
      builder_db=ctbc.BuilderDatabase.create(
        {
          fake_group: {
            fake_builder: ctbc.BuilderSpec.create(
              chromium_config='chromium',
              gclient_config='chromium',
              expose_trigger_properties=True,
            ),
            fake_triggered_builder: ctbc.BuilderSpec.create(
              chromium_config='chromium',
              gclient_config='chromium',
              expose_trigger_properties=True,
              parent_buildername=fake_builder,
              execution_mode=ctbc.TEST,
            ),
          }
        }
      ),
    ),
    api.chromium_tests.read_targets_spec(
      fake_group,
      {
        fake_triggered_builder: {
          'isolated_scripts': [
            {
              'name': fake_test,
              'swarming': {
                'dimensions': {
                  'os': 'Linux',
                },
              },
            }
          ],
        }
      },
    ),
    api.step_data(
      'read command lines', api.file.read_json({fake_test: ['command', 'args']})
    ),
    api.post_process(post_process.DoesNotRun, 'isolate tests'),
    api.post_process(
      LogDoesNotEqual, 'write command lines', 'command_lines.json', '{}'
    ),
    api.post_process(post_process.MustRun, 'archive command lines to RBE-CAS'),
    api.post_process(PropertyExists, 'trigger_properties'),
    api.post_process(post_process.DoesNotRun, 'mark: before_tests'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'ci_triggered_tester_use_test_trigger_cas',
    api.properties(
      config='Release',
      swarm_hashes={fake_test: 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeee/size'},
      test_trigger_deps_digest='fake-digest/123',
      parent_got_revision='fake-parent-revision',
    ),
    api.platform('linux', 64),
    api.chromium_tests_builder_config.ci_build(
      builder_group=fake_group,
      builder=fake_triggered_builder,
      builder_db=ctbc.BuilderDatabase.create(
        {
          fake_group: {
            fake_builder: ctbc.BuilderSpec.create(
              chromium_config='chromium',
              gclient_config='chromium',
            ),
            fake_triggered_builder: ctbc.BuilderSpec.create(
              chromium_config='chromium',
              gclient_config='chromium',
              parent_buildername=fake_builder,
              execution_mode=ctbc.TEST,
              use_test_trigger_cas=True,
            ),
          }
        }
      ),
    ),
    api.chromium_tests.read_targets_spec(
      fake_group,
      {
        fake_triggered_builder: {
          'isolated_scripts': [
            {
              'name': fake_test,
              'swarming': {
                'dimensions': {
                  'os': 'Linux',
                },
              },
            }
          ],
        }
      },
    ),
    api.post_process(post_process.DoesNotRun, 'bot_update'),
    api.post_process(post_process.MustRun, 'download test trigger CAS'),
    api.post_process(
      post_process.StepCommandContains, 'fake_test', ['--build-properties']
    ),
    api.post_process(post_process.DropExpectation),
  )

  # TODO(crbug.com/1174938): Remove this special case after
  # crbug.com/1166761 is fixed.
  yield api.test(
    'blink-web-tests-without-layout-results-handler-failure',
    api.chromium_tests_builder_config.ci_build(
      builder_group='fake-group',
      builder='fake-builder',
      builder_db=ctbc.BuilderDatabase.create(
        {
          'fake-group': {
            'fake-builder': ctbc.BuilderSpec.create(
              chromium_config='chromium',
              gclient_config='chromium',
            ),
          },
        }
      ),
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-builder': {
          'isolated_scripts': [
            {
              'name': 'blink_web_tests',
            }
          ],
        }
      },
    ),
    api.override_step_data(
      'blink_web_tests',
      api.test_utils.canned_isolated_script_output(
        isolated_script_passing=False
      ),
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'snoopy_reporting_enabled',
    api.properties(
      **{
        '$build/chromium_tests': {
          'enable_snoopy': True,
        }
      }
    ),
    api.chromium_tests_builder_config.ci_build(
      builder_group='fake-group',
      builder='fake-builder',
      builder_db=ctbc.BuilderDatabase.create(
        {
          'fake-group': {
            'fake-builder': ctbc.BuilderSpec.create(
              chromium_config='chromium',
              gclient_config='chromium',
            ),
          },
        }
      ),
    ),
    api.post_process(post_process.MustRun, 'snoop: report_stage'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'catch_snoopy_reporting_exception',
    api.properties(
      **{
        '$build/chromium_tests': {
          'enable_snoopy': True,
        }
      }
    ),
    api.chromium_tests_builder_config.ci_build(
      builder_group='fake-group',
      builder='fake-builder',
      builder_db=ctbc.BuilderDatabase.create(
        {
          'fake-group': {
            'fake-builder': ctbc.BuilderSpec.create(
              chromium_config='chromium',
              gclient_config='chromium',
            ),
          },
        }
      ),
    ),
    api.step_data('snoop: report_stage', retcode=1),
    api.step_data('snoop: report_stage (2)', retcode=1),
    api.step_data('snoop: report_stage (3)', retcode=1),
    api.step_data('snoop: report_stage (4)', retcode=1),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'ci_retry_passed_summary',
    api.platform('linux', 64),
    api.chromium.ci_build(builder_group='fake-group', builder='fake-builder'),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_ci_builder(
        builder_group='fake-group',
        builder='fake-builder',
        retry_failed_shards=True,
      ).assemble()
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-builder': {
          'gtest_tests': [
            {
              'test': 'base_unittests',
              'swarming': {
                'dimensions': {
                  'os': 'linux',
                },
              },
            }
          ],
        },
      },
    ),
    # If Test.One and Test.Two fail initially, and only Test.One passes on
    # retry, make sure Test.One doesn't show up in the summary.
    api.chromium_tests.gen_swarming_and_rdb_results(
      'base_unittests', '', skips=['Test.One', 'Test.Two']
    ),
    api.chromium_tests.gen_swarming_and_rdb_results(
      'base_unittests',
      'retry shards',
      successes=['Test.One'],
      failures=['Test.Two'],
    ),
    api.expect_status('FAILURE'),
    api.post_process(
      post_process.SummaryMarkdownRE,
      'failed because of:\s+- Test.Two',
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'pinlist_ci_bots',
    api.chromium.ci_build(
      builder_group='fake-group', builder='fake-webview-builder'
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_ci_builder(
        builder_group='fake-group',
        builder='fake-webview-builder',
        builder_spec=ctbc.BuilderSpec.create(
          gclient_config='chromium',
          chromium_config='chromium',
          chromium_config_kwargs={
            'TARGET_ARCH': 'arm',
            'TARGET_BITS': 64,
          },
        ),
      ).assemble()
    ),
    api.properties(
      swarm_hashes={
        'performance_test_suite': '[dummy hash for performance_test_suite/size]'
      },
    ),
    api.pinlist(upload_pinlist=True),
    api.platform('linux', 64),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-webview-builder': {
          'isolated_scripts': [
            {
              'name': 'performance_test_suite',
              'isolate_profile_data': True,
              'test': 'performance_test_suite',
              'swarming': {
                'dimensions': {
                  'os': 'Linux',
                },
              },
            }
          ],
        },
      },
    ),
    api.post_process(post_process.MustRun, 'processing generated pinlist'),
    api.post_process(post_process.DropExpectation),
  )
