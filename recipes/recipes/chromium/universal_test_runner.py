# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Triggers building and running tests for the Universal Test Runner"""

import itertools
from collections.abc import Iterable, Mapping
from google.protobuf import json_format

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
from RECIPE_MODULES.build.chromium_tests_builder_config import (
    builder_config as builder_config_module)

DEPS = [
    'chromium',
    'chromium_checkout',
    'chromium_tests',
    'chromium_tests_builder_config',
    'gn',
    'test_utils',
    'skylab',
    'depot_tools/gclient',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/step',
]

PROPERTIES = InputProperties


class RootBasePath(BasePath):
  """A base path for the root of the filesystem."""

  def resolve(self, test_enabled: bool) -> str:
    return ''


def RunSteps(api: RecipeApi, properties: InputProperties):
  should_build = properties.run_type != InputProperties.RunType.RUN_TYPE_RUN
  should_test = properties.run_type != InputProperties.RunType.RUN_TYPE_COMPILE

  (compiling_builder_id, compiling_builder_config, _,
   build_path) = configure_build(api, properties.checkout_path,
                                 properties.build_dir, should_build)

  if not properties.bypass_gclient:
    error_message = check_gclient(api)
    if error_message:
      rerun_props = InputProperties(bypass_gclient=True)
      return create_rerun_result(api, rerun_props, error_message,
                                 properties.output_properties_file)

  raw_result, tests = create_tests(api, build_path, properties.test_names,
                                   properties.got_revisions,
                                   compiling_builder_id,
                                   compiling_builder_config, should_build,
                                   properties.preserve_gn_args)
  if raw_result and raw_result.status != common_pb2.SUCCESS:
    return raw_result
  if not should_test:
    return result_pb2.RawResult(status=common_pb2.SUCCESS)

  test_runner = api.chromium_tests.create_test_runner(tests)
  with api.chromium_tests.wrap_chromium_tests(tests):
    api.chromium_tests.configure_swarming(True)
    return test_runner()


def get_gclient_config(api: RecipeApi):
  # TODO(crbug.com/41492686): The .gclient file can technically
  # exist in any parent dir but will normally be in the chromium
  # or chromium/src.
  checkout_file = api.path['checkout'].join('.gclient')
  src_file = api.path.split(api.path['checkout'])[0].join('.gclient')
  if api.path.exists(checkout_file):
    gclient_file_path = checkout_file
  elif api.path.exists(src_file):
    gclient_file_path = src_file
  else:
    raise FileNotFoundError(f'.gclient file not found at {str(checkout_file)} '
                            f'or {str(src_file)}')
  # TODO(https://crbug.com/327270127): Use some utility to get the current
  # .gclient config so we don't have to exec() the file
  gclient_text = api.file.read_text('read gclient', gclient_file_path)
  local_env = {}
  exec(gclient_text, {}, local_env)
  return local_env


def check_gclient(api: RecipeApi) -> str:
  mismatch_messages = []
  gclient_config = get_gclient_config(api)
  solution = [
      sol for sol in gclient_config.get('solutions', []) if sol.get('url', '')
      == 'https://chromium.googlesource.com/chromium/src.git'
  ]
  if len(solution) != 1:
    return ('Caution: your .gclient file could not be validated. Exactly one '
            'solution with \'url\' set to '
            'https://chromium.googlesource.com/chromium/src.git'
            ' must be set\n')
  solution = solution[0]

  current_custom_vars = solution.get('custom_vars', {})
  if 'rbe_instance' in current_custom_vars:
    mismatch_messages.append('- rbe_instance has been set in the .gclient file')

  if len(api.gclient.c.solutions) > 0:
    for builder_custom_var in api.gclient.c.solutions[0].custom_vars:
      if (builder_custom_var not in current_custom_vars or
          not current_custom_vars.get(builder_custom_var, {})):
        mismatch_messages.append(
            f'- custom_var {builder_custom_var} is not set in the local '
            '.gclient file')

  current_target_os = gclient_config.get('target_os', [])
  builder_target_os = api.gclient.c.target_os
  for os in builder_target_os:
    if os not in current_target_os:
      mismatch_messages.append(
          f'- target_os in builder config ({os}) is not in the local '
          f'.gclient file ({str(current_target_os)})')

  # TODO(crbug.com/41492686): Check custom_deps
  error_info = ''
  if mismatch_messages:
    error_info = ('Caution: your .gclient file and the builder\'s mismatches in'
                  ' the following way(s):\n' + '\n'.join(mismatch_messages))
  return error_info


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
  if not tests:
    raise api.step.StepFailure('No tests selected for running')

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
    build: bool) -> tuple[chromium.BuilderId, ctbc.BuilderConfig, Path, Path]:
  """Prepares the recipe to build with the provided checkout.

  Args:
      api: Recipe API object.
      checkout_dir: String to a chromium/src checkout that already has all
        intended updates or syncs.
      build_dir: String to a where the binaries are built
      build: Bool to configure the run to include compiling/building.

  Returns:
    Tuple of
      BuilderId for the compiler builder,
      BuilderConfig for the compiling builder
      checkout path,
      build path
  """
  builder = api.buildbucket.build.builder.builder
  builder_id = chromium.BuilderId.create_for_group(
      api.m.properties['builder_group'], builder)
  _, builder_config = (
      api.chromium_tests_builder_config.lookup_builder(use_try_db=True))

  # Default to assuming the builder both compiles and tests. But if it's
  # test-only, then we need to fetch its parent configs for use with mb/GN.
  compiling_builder_config = builder_config
  compiling_builder_id = builder_id
  if builder_config.execution_mode == ctbc.TEST:
    compiling_builder_id = chromium.BuilderId.create_for_group(
        builder_config.parent_builder_group, builder_config.parent_buildername)
    compiling_builder_config = builder_config_module.BuilderConfig.lookup(
        compiling_builder_id, builder_config.builder_db)
    if compiling_builder_config.execution_mode != ctbc.COMPILE_AND_TEST:
      raise api.step.StepFailure(
          f'Unsupported UTR invocation for builder {builder} triggered by '
          f'builder {builder_config.parent_buildername}. Please file a general '
          "infra bug via https://g.co/bugatrooper if you're seeing this.")

  api.chromium_tests.configure_build(builder_config, test_only=not build)
  api.path['checkout'] = api.path.abs_to_path(checkout_dir)
  api.chromium_checkout.checkout_dir = api.path['cache']

  build_dir = build_dir or api.path.join(api.path['checkout'], 'out',
                                         api.chromium.c.build_config_fs)
  build_path = Path(RootBasePath(), build_dir)
  api.chromium.output_dir = build_path
  return (compiling_builder_id, compiling_builder_config, api.path['checkout'],
          build_path)


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

  use_reclient = get_remote_compile_options(api, build_dir)

  # TODO(crbug.com/41492686): Remove if/when chromium.compile
  # can support developers' machines
  if use_reclient:
    with api.context(cwd=api.path['checkout']):
      # Ignore the builder job count and use a high number. This isn't run by
      # builders and more likely someone actively waiting for results
      step_result = api.step(
          name='reclient compile',
          cmd=[
              'ninja_reclient.py',
              '-C',
              api.path.relpath(build_dir, api.path['checkout']),
              '-j',
              '1000',
          ] + targets)
      return result_pb2.RawResult(status=step_result.presentation.status)
  return api.chromium.compile(
      targets, skip_log_upload=True, target_output_dir=build_dir)


def get_remote_compile_options(api, build_dir) -> bool:
  use_reclient = False
  if api.chromium.c.project_generator.tool == 'mb':
    gn_args, _ = api.gn.read_args(build_dir)
    args = api.gn.parse_gn_args(gn_args)
    use_reclient = args.get('use_remoteexec') == 'true'
  return use_reclient


def create_rerun_result(api: RecipeApi, rerun_properties: InputProperties,
                        info: str,
                        output_properties_file: str) -> result_pb2.RawResult:
  """Create a result for retriggering the recipe

  Writes the provided properties and creates a RawResult meant for the CLI to
  retrigger the recipe. The presence of rerun_properties should indicate to the
  CLI that the recipe can be invoked again differently for a different result.
  The info will be included in the RawResult meant to provide additional
  information to the user e.g. a prompt asking if gclient args from the builder
  are not set locally.

  Args:
      api: Recipe API object.
      rerun_properties: InputProperties that should overwrite properties on the
        next run
      info: Information string that the user should see
      output_properties_file: Where the rerun_properties should be written
  Returns:
    A RawResult that should be returned to trigger a rerun
  """
  if output_properties_file:
    api.file.write_json(
        'write output_properties_file', output_properties_file,
        json_format.MessageToDict(
            message=rerun_properties, preserving_proto_field_name=True))
  return result_pb2.RawResult(status=common_pb2.FAILURE, summary_markdown=info)


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
          checkout_path='[CACHE]/src',
          run_type=InputProperties.RunType.RUN_TYPE_COMPILE_AND_RUN,
          preserve_gn_args=False,
          disable_code_coverage=True,
          bypass_gclient=False,
      ),
      api.path.exists(api.path['cache'].join('.gclient')),
      api.step_data(
          'read gclient',
          api.file.read_text("""
solutions = [
  {
    'url': 'https://chromium.googlesource.com/chromium/src.git',
    'custom_vars': {
      'checkout_telemetry_dependencies': True,
    },
  },
]
""")),
      api.post_process(post_process.MustRun, 'compile'),
      api.post_process(post_process.MustRun, 'isolate'),
      api.post_process(post_process.MustRun, 'lookup GN args'),
      api.post_process(post_process.MustRun,
                       'test_pre_run.[trigger] browser_tests'),
      api.post_process(post_process.StepCommandContains, 'compile',
                       ['[CACHE]/src/out/Release', 'browser_tests']),
      api.post_process(post_process.StepCommandContains, 'isolate',
                       ['browser_tests']),
      api.post_process(
          post_process.StepCommandContains, 'isolate tests',
          ['[CACHE]/src/out/Release/browser_tests.isolated.gen.json']),
      api.post_process(post_process.StepCommandContains, 'generate_build_files',
                       ['-m', 'fake-group', '-b', 'fake-tester']),
      api.post_process(post_process.StepCommandContains, 'find command lines',
                       ['[CACHE]/src/out/Release']),
      api.post_process(post_process.MustRun, 'browser_tests'),
      api.post_process(post_process.DoesNotRun, 'upload_ninja_log'),
      api.post_process(post_process.StepCommandDoesNotContain, 'compile',
                       ['not_run_test']),
      api.post_process(
          post_process.StepCommandDoesNotContain, 'isolate tests',
          ['fake_root/fake_out/Debug/not_run_test.isolated.gen.json']),
      api.post_process(post_process.DoesNotRun, 'not_run_test'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'child_tester',
      api.chromium_tests_builder_config.ci_build(
          builder_group='fake-group',
          builder='fake-tester',
          builder_db=ctbc.BuilderDatabase.create({
              'fake-group': {
                  'fake-builder':
                      ctbc.BuilderSpec.create(
                          chromium_config='chromium',
                          gclient_config='chromium',
                      ),
                  'fake-tester':
                      ctbc.BuilderSpec.create(
                          execution_mode=ctbc.TEST,
                          parent_buildername='fake-builder',
                          parent_builder_group='fake-group',
                          chromium_config='chromium',
                          gclient_config='chromium',
                      ),
              },
          })),
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
      api.properties(
          test_names=['browser_tests'],
          checkout_path='[CACHE]/src',
          run_type=InputProperties.RunType.RUN_TYPE_COMPILE_AND_RUN,
          preserve_gn_args=False,
          disable_code_coverage=True,
          bypass_gclient=True,
      ),
      api.post_process(post_process.MustRun, 'browser_tests'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'unsupported_child_testers',
      api.chromium_tests_builder_config.ci_build(
          builder_group='fake-group',
          builder='fake-grandchild-tester',
          builder_db=ctbc.BuilderDatabase.create({
              'fake-group': {
                  'fake-builder':
                      ctbc.BuilderSpec.create(
                          chromium_config='chromium',
                          gclient_config='chromium',
                      ),
                  'fake-child-tester':
                      ctbc.BuilderSpec.create(
                          execution_mode=ctbc.TEST,
                          parent_buildername='fake-builder',
                          parent_builder_group='fake-group',
                          chromium_config='chromium',
                          gclient_config='chromium',
                      ),
                  'fake-grandchild-tester':
                      ctbc.BuilderSpec.create(
                          execution_mode=ctbc.TEST,
                          parent_buildername='fake-child-tester',
                          parent_builder_group='fake-group',
                          chromium_config='chromium',
                          gclient_config='chromium',
                      ),
              },
          })),
      api.properties(
          test_names=['browser_tests'],
          checkout_path='checkout',
          run_type=InputProperties.RunType.RUN_TYPE_COMPILE_AND_RUN,
          preserve_gn_args=False,
          disable_code_coverage=True,
      ),
      api.post_process(
          post_process.SummaryMarkdownRE,
          'Unsupported UTR invocation for builder fake-grandchild-tester.*',
      ),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'no_tests',
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
          test_names=['non_existant_test'],
          checkout_path='[CACHE]/src',
          run_type=InputProperties.RunType.RUN_TYPE_COMPILE_AND_RUN,
          preserve_gn_args=False,
          disable_code_coverage=True,
          bypass_gclient=True,
      ),
      api.post_process(
          post_process.SummaryMarkdown,
          'No tests selected for running',
      ),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'reclient',
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
          checkout_path='[CACHE]/src',
          build_dir='[CACHE]/src/out/Release',
          run_type=InputProperties.RunType.RUN_TYPE_COMPILE_AND_RUN,
          preserve_gn_args=False,
          disable_code_coverage=True,
          bypass_gclient=True,
      ),
      api.step_data('read GN args',
                    api.raw_io.output_text('use_remoteexec = true')),
      api.post_process(post_process.MustRun, 'reclient compile'),
      api.post_process(post_process.MustRun, 'isolate'),
      api.post_process(post_process.MustRun, 'lookup GN args'),
      api.post_process(post_process.MustRun,
                       'test_pre_run.[trigger] browser_tests'),
      api.post_process(post_process.StepCommandContains, 'reclient compile', [
          'ninja_reclient.py', '-C', 'out/Release', '-j', '1000',
          'browser_tests'
      ]),
      api.post_process(post_process.StepCommandContains, 'isolate',
                       ['browser_tests']),
      api.post_process(post_process.MustRun, 'browser_tests'),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'bad_gclient',
      ctbc_properties(
          builder_spec=ctbc.BuilderSpec.create(
              gclient_config='ios',
              chromium_config='chromium',
          )),
      api.chromium.generic_build(
          builder_group='fake-group',
          builder='fake-tester',
      ),
      api.properties(
          test_names=['browser_tests'],
          checkout_path='[CACHE]/src',
          run_type=InputProperties.RunType.RUN_TYPE_COMPILE_AND_RUN,
          preserve_gn_args=False,
          disable_code_coverage=True,
          bypass_gclient=False,
          output_properties_file='checkout/output_properties.json'),
      api.path.exists(api.path['cache'].join('src', '.gclient')),
      api.step_data(
          'read gclient',
          api.file.read_text("""
solutions = [
  {
    'url': 'https://chromium.googlesource.com/chromium/src.git',
    'custom_vars': {
      'rbe_instance': 'fake_instance',
    },
  },
]
target_os=['os']
""")),
      api.post_process(
          post_process.ResultReason,
          'Caution: your .gclient file and the builder\'s mismatches in the '
          'following way(s):\n'
          '- rbe_instance has been set in the .gclient file\n'
          '- custom_var checkout_telemetry_dependencies is not set in the '
          'local .gclient file\n'
          '- target_os in builder config (ios) is not in the local .gclient '
          'file ([\'os\'])'),
      api.post_process(post_process.StepCommandContains, 'read gclient',
                       ['[CACHE]/src/.gclient']),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'gclient_above_src',
      ctbc_properties(
          builder_spec=ctbc.BuilderSpec.create(
              gclient_config='chromium',
              chromium_config='chromium',
          )),
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
          checkout_path='[CACHE]/src',
          run_type=InputProperties.RunType.RUN_TYPE_COMPILE_AND_RUN,
          preserve_gn_args=False,
          disable_code_coverage=True,
          bypass_gclient=False,
      ),
      api.path.exists(api.path['cache'].join('.gclient')),
      api.step_data(
          'read gclient',
          api.file.read_text("""
solutions = [
  {
    'url': 'https://chromium.googlesource.com/chromium/src.git',
    'custom_vars': {
      'checkout_telemetry_dependencies': True,
    },
  },
]
""")),
      api.post_process(post_process.StepCommandContains, 'read gclient',
                       ['[CACHE]/.gclient']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'src_not_in_gclient',
      ctbc_properties(
          builder_spec=ctbc.BuilderSpec.create(
              gclient_config='ios',
              chromium_config='chromium',
          )),
      api.chromium.generic_build(
          builder_group='fake-group',
          builder='fake-tester',
      ),
      api.properties(
          test_names=['browser_tests'],
          checkout_path='[CACHE]/src',
          run_type=InputProperties.RunType.RUN_TYPE_COMPILE_AND_RUN,
          preserve_gn_args=False,
          disable_code_coverage=True,
          bypass_gclient=False,
          output_properties_file='checkout/output_properties.json'),
      api.path.exists(api.path['cache'].join('src', '.gclient')),
      api.step_data(
          'read gclient',
          api.file.read_text("""
solutions = [
  {
    'custom_vars': {
      'rbe_instance': 'fake_instance',
    },
  },
]
target_os=['os']
""")),
      api.post_process(
          post_process.ResultReason,
          'Caution: your .gclient file could not be validated. Exactly one '
          'solution with \'url\' set to '
          'https://chromium.googlesource.com/chromium/src.git must be set\n'),
      api.post_process(post_process.StepCommandContains, 'read gclient',
                       ['[CACHE]/src/.gclient']),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'missing_gclient',
      ctbc_properties(
          builder_spec=ctbc.BuilderSpec.create(
              gclient_config='chromium',
              chromium_config='chromium',
          )),
      api.chromium.generic_build(
          builder_group='fake-group',
          builder='fake-tester',
      ),
      api.properties(
          test_names=['browser_tests'],
          checkout_path='[CACHE]/src',
          run_type=InputProperties.RunType.RUN_TYPE_COMPILE_AND_RUN,
          preserve_gn_args=False,
          disable_code_coverage=True,
          bypass_gclient=False,
      ),
      api.expect_exception('FileNotFoundError'),
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
          checkout_path='[CACHE]/src',
          run_type=InputProperties.RunType.RUN_TYPE_COMPILE_AND_RUN,
          preserve_gn_args=False,
          disable_code_coverage=True,
          build_dir='fake_root/fake_out/Debug',
          bypass_gclient=True,
      ),
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
          checkout_path='[CACHE]/src',
          run_type=InputProperties.RunType.RUN_TYPE_COMPILE_AND_RUN,
          preserve_gn_args=False,
          bypass_gclient=True,
      ),
      api.post_process(post_process.MustRun, 'compile'),
      api.post_process(post_process.MustRun, 'isolate'),
      api.post_process(post_process.MustRun,
                       'test_pre_run.[trigger] browser_tests'),
      api.post_process(post_process.StepCommandContains, 'generate_build_files',
                       ['-m', 'fake-group', '-b', 'fake-tester']),
      api.post_process(post_process.MustRun, 'browser_tests'),
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
          checkout_path='[CACHE]/src',
          run_type=InputProperties.RunType.RUN_TYPE_COMPILE_AND_RUN,
          preserve_gn_args=False,
          bypass_gclient=True,
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
          checkout_path='[CACHE]/src',
          run_type=InputProperties.RunType.RUN_TYPE_COMPILE_AND_RUN,
          preserve_gn_args=False,
          bypass_gclient=True,
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
          checkout_path='[CACHE]/src',
          run_type=InputProperties.RunType.RUN_TYPE_COMPILE_AND_RUN,
          preserve_gn_args=True,
          bypass_gclient=True,
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
          checkout_path='[CACHE]/src',
          run_type=InputProperties.RunType.RUN_TYPE_RUN,
          bypass_gclient=True,
      ),
      api.post_process(post_process.DoesNotRun, 'compile'),
      api.post_process(post_process.DoesNotRun, 'generate_build_files'),
      api.post_process(post_process.MustRun, 'isolate'),
      api.post_process(post_process.MustRun,
                       'test_pre_run.[trigger] browser_tests'),
      api.post_process(post_process.MustRun, 'browser_tests'),
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
          checkout_path='[CACHE]/src',
          run_type=InputProperties.RunType.RUN_TYPE_COMPILE,
          preserve_gn_args=False,
          bypass_gclient=True,
      ),
      api.post_process(post_process.MustRun, 'compile'),
      api.post_process(post_process.MustRun, 'isolate'),
      api.post_process(post_process.StepCommandContains, 'generate_build_files',
                       ['-m', 'fake-group', '-b', 'fake-tester']),
      api.post_process(post_process.DoesNotRun,
                       'test_pre_run.[trigger] browser_tests'),
      api.post_process(post_process.DoesNotRun, 'browser_tests'),
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
          checkout_path='[CACHE]/src',
          run_type=InputProperties.RunType.RUN_TYPE_COMPILE_AND_RUN,
          build_dir='fake_root/fake_out/Debug',
          bypass_gclient=True,
      ),
      api.path.exists(
          Path(RootBasePath(), 'fake_root/fake_out/Debug/lacros-test.isolate'),
          api.path['start_dir'].join('squashfs', 'squashfs-tools',
                                     'mksquashfs'),
      ),
      api.step_data(
          'prepare skylab tests.collect runtime deps for lacros-test.read '
          'isolate file', api.file.read_text(SKYLAB_ISOLATE_TEXT)),
      api.skylab.mock_wait_on_suites('lacros-test', 1),
      api.override_step_data(
          'lacros-test results',
          stdout=api.raw_io.output_text(
              api.test_utils.rdb_results(
                  'lacros-test', passing_tests=['Test.One']))),
      api.post_process(post_process.MustRun, 'compile'),
      api.post_process(post_process.MustRun, 'generate_build_files'),
      api.post_process(post_process.MustRun,
                       'test_pre_run.lacros-test.schedule'),
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
      api.post_process(post_process.DropExpectation),
  )
