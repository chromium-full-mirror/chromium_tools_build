# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Triggers building and running tests for the Universal Test Runner"""

import itertools

from recipe_engine import post_process
from recipe_engine.config_types import Path, BasePath

from PB.recipe_engine import result as result_pb2
from PB.recipes.build.chromium.universal_test_runner import InputProperties
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb2
from RECIPE_MODULES.build import chromium

DEPS = [
    'chromium',
    'chromium_tests',
    'chromium_tests_builder_config',
    'gn',
    'recipe_engine/buildbucket',
    'recipe_engine/path',
    'recipe_engine/properties',
]

PROPERTIES = InputProperties


class RootBasePath(BasePath):
  """A base path for the root of the filesystem."""

  def resolve(self, test_enabled: bool) -> str:
    return ''


def RunSteps(api, properties):
  should_build = properties.run_type != InputProperties.RunType.RUN_TYPE_RUN
  should_test = properties.run_type != InputProperties.RunType.RUN_TYPE_COMPILE

  checkout_path = Path(RootBasePath(), properties.checkout_path)
  builder_id, builder_config = configure_build(api, checkout_path, should_build)

  build_dir = properties.build_dir or '//out/%s' % api.chromium.c.build_config_fs
  build_dir = Path(RootBasePath(), build_dir)
  raw_result, tests = create_tests(api, build_dir, properties.test_names,
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


def create_tests(api, build_dir, test_names, got_revisions, builder_id,
                 builder_config, should_build, preserve_gn_args):
  """Creates and potentially compiles the test for the builder/test names

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
  if isolate_tests:
    mb_args = ['--no-build']
    mb_args.append(build_dir)
    mb_args.extend([test.target_name for test in tests if test.isolate_target])
    # Pass an empty builder_id to prevent gn_args from getting reapplied. They
    # should have been set in the mb gen or should not be overwritten
    api.chromium.run_mb_cmd(
        'isolate',
        'isolate',
        builder_id=None,
        additional_args=mb_args,
    )
    api.chromium_tests.isolate_tests(
        builder_config, isolate_tests, '', '', build_dir=build_dir)

  return None, tests


def configure_build(api, checkout_path, build):
  """Prepares the recipe to build with the provided checkout.

  Args:
      api: Recipe API object.
      checkout_path: Path to a chromium/src checkout that already has all
        intended updates or syncs.
      build: Bool to configure the run to include compiling/building.
  """
  builder = api.buildbucket.build.builder.builder
  builder_id = chromium.BuilderId.create_for_group(
      api.m.properties['builder_group'], builder)
  _, builder_config = (
      api.chromium_tests_builder_config.lookup_builder(use_try_db=True))
  # TODO(crbug.com/1519709): test_only should be based on run mode
  api.chromium_tests.configure_build(builder_config, test_only=not build)
  api.path['checkout'] = checkout_path
  return builder_id, builder_config


def compile_targets(api, tests, builder_id, preserve_gn_args, build_dir):
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


def GenTests(api):
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
                       ['//out/Release']),
      api.post_process(post_process.StepCommandContains, 'isolate tests',
                       ['//out/Release/browser_tests.isolated.gen.json']),
      api.post_process(post_process.StepCommandContains, 'generate_build_files',
                       ['-m', 'fake-group', '-b', 'fake-tester']),
      api.post_process(post_process.StepCommandContains, 'find command lines',
                       ['//out/Release']),
      api.post_process(post_process.MustRun, 'browser_tests'),
      api.post_process(post_process.DoesNotRun, 'upload_ninja_log'),
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
                       ['fake_root/fake_out/Debug']),
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
