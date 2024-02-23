# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Triggers building and running tests for the Universal Test Runner"""

import itertools
from collections.abc import Iterable, Mapping

from recipe_engine import post_process
from recipe_engine.config_types import Path, BasePath
from recipe_engine.recipe_api import RecipeApi
from recipe_engine.recipe_test_api import RecipeTestApi

from PB.recipe_engine import result as result_pb2
from PB.recipes.build.chromium.universal_test_runner import InputProperties
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb2
from RECIPE_MODULES.build import chromium
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc
from RECIPE_MODULES.build.chromium_tests.steps import Test

DEPS = [
    'chromium',
    'chromium_checkout',
    'chromium_tests',
    'chromium_tests_builder_config',
    'gn',
    'test_utils',
    'skylab',
    'recipe_engine/buildbucket',
    'recipe_engine/file',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
]

PROPERTIES = InputProperties


class RootBasePath(BasePath):
  """A base path for the root of the filesystem."""

  def resolve(self, test_enabled: bool) -> str:
    return ''


def RunSteps(api: RecipeApi, properties: InputProperties):
  should_build = properties.run_type != InputProperties.RunType.RUN_TYPE_RUN
  should_test = properties.run_type != InputProperties.RunType.RUN_TYPE_COMPILE

  builder_id, builder_config, _, build_path = configure_build(
      api, properties.checkout_path, properties.build_dir, should_build)

  raw_result, tests = create_tests(api, build_path, properties.test_names,
                                   properties.got_revisions, builder_id,
                                   builder_config, should_build,
                                   properties.preserve_gn_args)
  if raw_result and raw_result.status != common_pb2.SUCCESS:
    return raw_result
  if not should_test:
    return result_pb2.RawResult(status=common_pb2.SUCCESS)

  test_runner = api.chromium_tests.create_test_runner(tests)
  with api.chromium_tests.wrap_chromium_tests(builder_config, tests):
    api.chromium_tests.configure_swarming(True, builder_group=builder_id.group)
    return test_runner()


def create_tests(
    api: RecipeApi,
    build_dir: Path,
    test_names: Iterable[str],
    got_revisions: Mapping[str, str],
    builder_id: chromium.BuilderId,
    builder_config: ctbc.BuilderConfig,
    should_build: bool,
    preserve_gn_args: bool,
) -> tuple[result_pb2.RawResult, Iterable[Test]]:
  """Creates the test objects for the provided builder/test names

  Args:
      api: Recipe API object.
      build_dir: Path to the build directory either containing the prebuilt
        binaries or where they should be built
      test_names: Names of the tests to create on the provided builder_id
      got_revisions: Mapping[str, str] of the revisions retrieved during updates
      builder_id: The ID of the builder to get tests from
      builder_config: A BuilderConfig with the configuration for the builder
        being reproduced
      should_build: Bool controlling whether the tests should be compiled
      preserve_gn_args: Bool whether to have the recipe overwrite the gn args
        with the builder_id's gn args
  """
  targets_config = api.chromium_tests.create_targets_config(
      builder_config,
      got_revisions,
      api.path['checkout'],
      targets_spec_dir=api.path['checkout'].join('testing', 'buildbot'))
  tests = [test for test in targets_config.all_tests if test.name in test_names]

  if should_build:
    raw_result = compile_targets(api, tests, builder_id, preserve_gn_args,
                                 build_dir)
    if raw_result.status != common_pb2.SUCCESS:
      return raw_result, None

  isolate_tests = [test for test in tests if test.isolate_target]
  skylab_tests = [test for test in tests if test.is_skylabtest]
  if isolate_tests or skylab_tests:
    mb_args = ['--no-build']
    mb_args.append(build_dir)
    mb_args.extend([test.target_name for test in isolate_tests + skylab_tests])
    # Pass an empty builder_id to prevent gn_args from getting reapplied. They
    # should have been set in the mb gen or should not be overwritten
    api.chromium.run_mb_cmd(
        'isolate',
        'isolate',
        builder_id=None,
        additional_args=mb_args,
    )
  if isolate_tests:
    api.chromium_tests.isolate_tests(
        builder_config, isolate_tests, '', '', build_dir=build_dir)

  if skylab_tests:
    api.chromium_tests.prepare_artifact_for_skylab(
        builder_config, skylab_tests, phase='')
  return None, tests


def configure_build(
    api: RecipeApi, checkout_dir: str, build_dir: str,
    build: bool) -> tuple[chromium.BuilderId, ctbc.BuilderConfig]:
  """Prepares the recipe to build with the provided checkout.

  Args:
      api: Recipe API object.
      checkout_dir: String to a chromium/src checkout that already has all
        intended updates or syncs.
      build_dir: String to a where the binaries are built
      build: Bool to configure the run to include compiling/building.
  """
  builder = api.buildbucket.build.builder.builder
  builder_id = chromium.BuilderId.create_for_group(
      api.m.properties['builder_group'], builder)
  _, builder_config = (
      api.chromium_tests_builder_config.lookup_builder(use_try_db=True))
  checkout_path = Path(RootBasePath(), checkout_dir)
  api.chromium_tests.configure_build(builder_config, test_only=not build)
  api.path['checkout'] = checkout_path.join('src')
  api.chromium_checkout.checkout_dir = checkout_path

  build_dir = build_dir or '//out/%s' % api.chromium.c.build_config_fs
  build_path = Path(RootBasePath(), build_dir)
  api.chromium.output_dir = build_path
  return builder_id, builder_config, checkout_path, build_path


def compile_targets(
    api: RecipeApi,
    tests: Iterable[Test],
    builder_id: chromium.BuilderId,
    preserve_gn_args: bool,
    build_dir: str,
) -> result_pb2.RawResult:
  """Builds the test targets

  Args:
      api: Recipe API object.
      tests: Iterable of test objects to be compiled
      builder_id: The ID of the builder to compile for
      preserve_gn_args: Bool whether to have the recipe overwrite the gn args
        with the builder_id's gn args
      build_dir: Path to the directory to use for building
  """
  targets = list(itertools.chain(*[t.compile_targets() for t in tests]))

  # Remove duplicate targets.
  targets = sorted(set(targets))

  if preserve_gn_args:
    api.gn.gen(build_dir, 'gn_gen')
  else:
    api.chromium.mb_gen(
        builder_id,
        name='generate_build_files',
        recursive_lookup=True,
        build_dir=build_dir)

  return api.chromium.compile(
      targets, skip_log_upload=True, target_output_dir=build_dir)


def GenTests(api: RecipeTestApi):
  ctbc_api = api.chromium_tests_builder_config

  def ctbc_properties(builder_spec=None):
    return ctbc_api.properties(
        ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
            builder_group='fake-group',
            builder='fake-builder',
            builder_spec=builder_spec,
        ).with_mirrored_tester(
            builder_group='fake-group',
            builder='fake-tester',
        ).assemble())

  yield api.test(
      'basic',
      ctbc_properties(),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-tester': {
                  'gtest_tests': [{
                      'name': 'browser_tests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                              'pool': 'fake-pool',
                          },
                      },
                  }, {
                      'name': 'not_run_test',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                              'pool': 'fake-pool',
                          },
                      },
                  }],
              },
          }),
      api.chromium.generic_build(
          builder_group='fake-group',
          builder='fake-tester',
      ),
      api.properties(
          test_names=['browser_tests'],
          checkout_path='checkout',
          run_type=InputProperties.RunType.RUN_TYPE_COMPILE_AND_RUN,
          preserve_gn_args=False,
          disable_code_coverage=True,
      ),
      api.post_process(post_process.MustRun, 'compile'),
      api.post_process(post_process.MustRun, 'isolate'),
      api.post_process(post_process.MustRun, 'lookup GN args'),
      api.post_process(post_process.MustRun,
                       'test_pre_run.[trigger] browser_tests'),
      api.post_process(post_process.StepCommandContains, 'compile',
                       ['//out/Release', 'browser_tests']),
      api.post_process(post_process.StepCommandContains, 'isolate',
                       ['browser_tests']),
      api.post_process(post_process.StepCommandContains, 'isolate tests',
                       ['//out/Release/browser_tests.isolated.gen.json']),
      api.post_process(post_process.StepCommandContains, 'generate_build_files',
                       ['-m', 'fake-group', '-b', 'fake-tester']),
      api.post_process(post_process.StepCommandContains, 'find command lines',
                       ['//out/Release']),
      api.post_process(post_process.MustRun, 'browser_tests'),
      api.post_process(post_process.DoesNotRun, 'upload_ninja_log'),
      api.post_process(post_process.StepCommandDoesNotContain, 'compile',
                       ['not_run_test']),
      api.post_process(
          post_process.StepCommandDoesNotContain, 'isolate tests',
          ['fake_root/fake_out/Debug/not_run_test.isolated.gen.json']),
      api.post_process(post_process.DoesNotRun, 'not_run_test'),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'changed_build_dir',
      ctbc_properties(),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-tester': {
                  'gtest_tests': [{
                      'name': 'browser_tests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                              'pool': 'fake-pool',
                          },
                      },
                  }],
              },
          }),
      api.chromium.generic_build(
          builder_group='fake-group',
          builder='fake-tester',
      ),
      api.properties(
          test_names=['browser_tests'],
          checkout_path='checkout',
          run_type=InputProperties.RunType.RUN_TYPE_COMPILE_AND_RUN,
          preserve_gn_args=False,
          disable_code_coverage=True,
          build_dir='fake_root/fake_out/Debug'),
      api.post_process(post_process.MustRun, 'compile'),
      api.post_process(post_process.MustRun, 'isolate'),
      api.post_process(post_process.MustRun, 'lookup GN args'),
      api.post_process(post_process.MustRun,
                       'test_pre_run.[trigger] browser_tests'),
      api.post_process(post_process.StepCommandContains, 'compile',
                       ['fake_root/fake_out/Debug', 'browser_tests']),
      api.post_process(
          post_process.StepCommandContains, 'isolate tests',
          ['fake_root/fake_out/Debug/browser_tests.isolated.gen.json']),
      api.post_process(post_process.StepCommandContains, 'find command lines',
                       ['fake_root/fake_out/Debug']),
      api.post_process(post_process.MustRun, 'browser_tests'),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'no_compile_targets',
      ctbc_properties(),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-tester': {
                  'gtest_tests': [{
                      'name': 'browser_tests',
                      'test': None,
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                              'pool': 'fake-pool',
                          },
                      },
                  }],
              },
          }),
      api.chromium.generic_build(
          builder_group='fake-group',
          builder='fake-tester',
      ),
      api.properties(
          test_names=['browser_tests'],
          checkout_path='checkout',
          run_type=InputProperties.RunType.RUN_TYPE_COMPILE_AND_RUN,
          preserve_gn_args=False,
      ),
      api.post_process(post_process.MustRun, 'compile'),
      api.post_process(post_process.MustRun, 'isolate'),
      api.post_process(post_process.MustRun,
                       'test_pre_run.[trigger] browser_tests'),
      api.post_process(post_process.StepCommandContains, 'generate_build_files',
                       ['-m', 'fake-group', '-b', 'fake-tester']),
      api.post_process(post_process.MustRun, 'browser_tests'),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'failed_test',
      ctbc_properties(),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-tester': {
                  'gtest_tests': [{
                      'name': 'browser_tests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                              'pool': 'fake-pool',
                          },
                      },
                  }],
              },
          }),
      api.chromium.generic_build(
          builder_group='fake-group',
          builder='fake-tester',
      ),
      api.properties(
          test_names=['browser_tests'],
          checkout_path='checkout',
          run_type=InputProperties.RunType.RUN_TYPE_COMPILE_AND_RUN,
          preserve_gn_args=False,
      ),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', '', failures=['Test.One']),
      api.post_process(post_process.MustRun, 'compile'),
      api.post_process(post_process.MustRun, 'isolate'),
      api.post_process(post_process.MustRun,
                       'test_pre_run.[trigger] browser_tests'),
      api.post_process(post_process.StepCommandContains, 'generate_build_files',
                       ['-m', 'fake-group', '-b', 'fake-tester']),
      api.post_process(post_process.MustRun, 'browser_tests'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'failed_build',
      ctbc_properties(),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-tester': {
                  'gtest_tests': [{
                      'name': 'browser_tests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                              'pool': 'fake-pool',
                          },
                      },
                  }],
              },
          }),
      api.chromium.generic_build(
          builder_group='fake-group',
          builder='fake-tester',
      ),
      api.properties(
          test_names=['browser_tests'],
          checkout_path='checkout',
          run_type=InputProperties.RunType.RUN_TYPE_COMPILE_AND_RUN,
          preserve_gn_args=False,
      ),
      api.override_step_data('compile', retcode=1),
      api.post_process(post_process.MustRun, 'compile'),
      api.post_process(post_process.DoesNotRun,
                       'test_pre_run.[trigger] browser_tests'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'preserve_gn_args',
      ctbc_properties(),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-tester': {
                  'gtest_tests': [{
                      'name': 'browser_tests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                              'pool': 'fake-pool',
                          },
                      },
                  }],
              },
          }),
      api.chromium.generic_build(
          builder_group='fake-group',
          builder='fake-tester',
      ),
      api.properties(
          test_names=['browser_tests'],
          checkout_path='checkout',
          run_type=InputProperties.RunType.RUN_TYPE_COMPILE_AND_RUN,
          preserve_gn_args=True,
      ),
      api.post_process(post_process.MustRun, 'compile'),
      api.post_process(post_process.MustRun, 'isolate'),
      api.post_process(post_process.MustRun,
                       'test_pre_run.[trigger] browser_tests'),
      api.post_process(post_process.MustRun, 'gn_gen'),
      api.post_process(post_process.DoesNotRun, 'generate_build_files'),
      api.post_process(post_process.StepCommandDoesNotContain, 'isolate',
                       ['-m', 'fake-group', '-b', 'fake-tester']),
      api.post_process(post_process.DoesNotRun, 'lookup GN args'),
      api.post_process(post_process.MustRun, 'browser_tests'),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'skip_build',
      ctbc_properties(),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-tester': {
                  'gtest_tests': [{
                      'name': 'browser_tests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                              'pool': 'fake-pool',
                          },
                      },
                  }],
              },
          }),
      api.chromium.generic_build(
          builder_group='fake-group',
          builder='fake-tester',
      ),
      api.properties(
          test_names=['browser_tests'],
          checkout_path='checkout',
          run_type=InputProperties.RunType.RUN_TYPE_RUN,
      ),
      api.post_process(post_process.DoesNotRun, 'compile'),
      api.post_process(post_process.DoesNotRun, 'generate_build_files'),
      api.post_process(post_process.MustRun, 'isolate'),
      api.post_process(post_process.MustRun,
                       'test_pre_run.[trigger] browser_tests'),
      api.post_process(post_process.MustRun, 'browser_tests'),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'skip_test',
      ctbc_properties(),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-tester': {
                  'gtest_tests': [{
                      'name': 'browser_tests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                              'pool': 'fake-pool',
                          },
                      },
                  }],
              },
          }),
      api.chromium.generic_build(
          builder_group='fake-group',
          builder='fake-tester',
      ),
      api.properties(
          test_names=['browser_tests'],
          checkout_path='checkout',
          run_type=InputProperties.RunType.RUN_TYPE_COMPILE,
          preserve_gn_args=False,
      ),
      api.post_process(post_process.MustRun, 'compile'),
      api.post_process(post_process.MustRun, 'isolate'),
      api.post_process(post_process.StepCommandContains, 'generate_build_files',
                       ['-m', 'fake-group', '-b', 'fake-tester']),
      api.post_process(post_process.DoesNotRun,
                       'test_pre_run.[trigger] browser_tests'),
      api.post_process(post_process.DoesNotRun, 'browser_tests'),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  SKYLAB_ISOLATE_TEXT = """
    {'variables': {'command': ['bin/run_lacros_smoke_tast_tests',
                            '--logs-dir=${ISOLATED_OUTDIR}'],
                'files': ['../../.vpython',
                          'bin/run_vaapi_unittest',
                          'resources.pak',
                          'resources.pak.info',
                          './chrome',
                          '../../testing/buildbot/filters',
                          'gen/third_party',
                          '../../testing/buildbot/filters',
                          'bin/lacros_fyi_tast_tests.filter'
                            ]}}
  """

  yield api.test(
      'skylab_test',
      ctbc_properties(
          builder_spec=ctbc.BuilderSpec.create(
              gclient_config='chromium',
              chromium_config='chromium',
              skylab_gs_bucket='chrome-test-builds',
          ),),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-tester': {
                  'skylab_tests': [{
                      'name': 'lacros-test',
                      'cros_board': 'volteer',
                      'lacros_gcs_path': 'lacros_gcs_path',
                  }],
              },
          }),
      api.chromium.generic_build(
          builder_group='fake-group',
          builder='fake-tester',
      ),
      api.properties(
          test_names=['lacros-test'],
          checkout_path='checkout',
          run_type=InputProperties.RunType.RUN_TYPE_COMPILE_AND_RUN,
          build_dir='fake_root/fake_out/Debug'),
      api.path.exists(
          Path(RootBasePath(), 'fake_root/fake_out/Debug/lacros-test.isolate'),
          api.path['start_dir'].join('squashfs', 'squashfs-tools',
                                     'mksquashfs'),
      ),
      api.step_data(
          'prepare skylab tests.collect runtime deps for lacros-test.read '
          'isolate file', api.file.read_text(SKYLAB_ISOLATE_TEXT)),
      api.skylab.mock_wait_on_suites('find test runner build', 1),
      api.override_step_data(
          'lacros-test results',
          stdout=api.raw_io.output_text(
              api.test_utils.rdb_results(
                  'lacros-test', passing_tests=['Test.One']))),
      api.post_process(post_process.MustRun, 'compile'),
      api.post_process(post_process.MustRun, 'generate_build_files'),
      api.post_process(post_process.MustRun,
                       'test_pre_run.schedule skylab tests.lacros-test'),
      api.post_process(post_process.MustRun, 'lacros-test'),
      api.post_process(
          post_process.MustRun,
          'prepare skylab tests.collect runtime deps for lacros-test'),
      api.post_process(post_process.StepCommandContains, 'compile',
                       ['fake_root/fake_out/Debug', 'lacros-test']),
      api.post_process(
          post_process.StepCommandContains,
          'prepare skylab tests.collect runtime deps for lacros-test.read '
          'isolate file', ['fake_root/fake_out/Debug/lacros-test.isolate']),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )
