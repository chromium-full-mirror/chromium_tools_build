# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Triggers building and running tests for the Universal Test Runner"""

import attr
import copy
import itertools
from collections.abc import Iterable, Mapping
from google.protobuf import json_format

from recipe_engine import post_process
from recipe_engine.config_types import Path
from recipe_engine.recipe_api import RecipeApi
from recipe_engine.recipe_test_api import RecipeTestApi

from PB.recipe_engine import result as result_pb2
from PB.recipes.build.chromium.universal_test_runner import InputProperties
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb2
from RECIPE_MODULES.build import chromium
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc
from RECIPE_MODULES.build.chromium_tests.steps import (
    Test, SwarmingIsolatedScriptTest)
from RECIPE_MODULES.build.chromium_tests_builder_config import (
    builder_config as builder_config_module)

DEPS = [
    'chromium',
    'chromium_checkout',
    'chromium_swarming',
    'chromium_tests',
    'chromium_tests_builder_config',
    'code_coverage',
    'gn',
    'test_utils',
    'reclient',
    'skylab',
    'depot_tools/gclient',
    'depot_tools/git',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/step',
]

PROPERTIES = InputProperties


try:  # pragma: no cover
  # BUG(329113288) - old-style path types
  #
  # Note that the old and new paths are not covered simultaneously, so both have
  # to be nocover in order to allow a non-trivial roll rather than a manual one.
  # Once the upstream change which deletes the BasePath type and adds
  # api.path.cast_to_path land, this whole block and the OLD_PATH_TYPE==True
  # block in the body of configure_build can be deleted.
  from recipe_engine.config_types import BasePath
  class RootBasePath(BasePath):
    """A base path for the root of the filesystem."""

    def resolve(self, test_enabled: bool) -> str:
      return ''
  OLD_PATH_TYPE = True
except ImportError:  # pragma: no cover
  OLD_PATH_TYPE = False


def RunSteps(api: RecipeApi, properties: InputProperties):
  should_build = properties.run_type != InputProperties.RunType.RUN_TYPE_RUN
  should_test = properties.run_type != InputProperties.RunType.RUN_TYPE_COMPILE

  (compiling_builder_id, compiling_builder_config, _,
   build_path) = configure_build(api, properties.checkout_path,
                                 properties.build_dir, should_build)

  result = prerun_checks(api, properties, build_path, compiling_builder_id)
  if result != None:
    return result

  got_revisions = generate_got_revisions_map(api)

  raw_result, tests = create_tests(api, properties, build_path, got_revisions,
                                   compiling_builder_id,
                                   compiling_builder_config, should_build)
  if raw_result and raw_result.status != common_pb2.SUCCESS:
    return raw_result
  if not should_test:
    return result_pb2.RawResult(status=common_pb2.SUCCESS)

  test_runner = api.chromium_tests.create_test_runner(tests)
  with api.chromium_tests.wrap_chromium_tests(tests):
    api.chromium_tests.configure_swarming(True)
    # Lower pri for faster turn-around time in debugging. The UTR shouldn't
    # get so much use that it affects CI/CQ traffic substantially. But we can
    # check for sure using the UTR-specific tag below, and reassess if needed.
    api.chromium_swarming.default_priority = 20
    api.chromium_swarming.add_default_tag('is_utr:1')
    return test_runner()


def prerun_checks(
    api: RecipeApi, properties: InputProperties, build_path: Path,
    compiling_builder_id: chromium.BuilderId) -> result_pb2.RawResult:
  # TODO(crbug.com/41492686): Combine these checks so they can be prompted in
  # one interation of the recipe invocations
  def create_prompt_option(prompt: str, **kwargs):
    if not kwargs:
      return (prompt, InputProperties())
    rerun_properties = copy.deepcopy(properties.rerun_options)
    update = InputProperties.RerunOptions(**kwargs)
    rerun_properties.MergeFrom(update)
    return (prompt, rerun_properties)

  if not properties.rerun_options.bypass_gclient:
    error_message = check_gclient(api)
    if error_message:
      rerun_options = [
          create_prompt_option('yes', bypass_gclient=True),
          create_prompt_option('no')
      ]
      return create_rerun_result(api, rerun_options, error_message,
                                 properties.output_properties_file)
  if (api.path.exists(build_path.join('args.gn')) and
      not properties.rerun_options.bypass_gn_args):
    error_message = check_gn_args(api, build_path, compiling_builder_id)
    if error_message:
      rerun_options = [
          create_prompt_option(
              'continue', bypass_gn_args=True, preserve_gn_args=True),
          create_prompt_option(
              'overwrite', bypass_gn_args=True, preserve_gn_args=False),
          create_prompt_option('abort')
      ]
      return create_rerun_result(api, rerun_options, error_message,
                                 properties.output_properties_file)
  if (not properties.rerun_options.bypass_branch_check and
      api.code_coverage.using_coverage and properties.builder_recipe
      in ('chromium/orchestrator', 'chromium_trybot')):
    error_message = check_upstream_branch(api)
    if error_message:
      rerun_options = [
          create_prompt_option(
              'instrument everything',
              bypass_branch_check=True,
              skip_instrumentation=False),
          create_prompt_option(
              'skip instrumentation',
              bypass_branch_check=True,
              skip_instrumentation=True),
          create_prompt_option('abort')
      ]
      return create_rerun_result(api, rerun_options, error_message,
                                 properties.output_properties_file)


def get_gclient_config(api: RecipeApi):
  # TODO(crbug.com/41492686): The .gclient file can technically
  # exist in any parent dir but will normally be in the chromium
  # or chromium/src.
  checkout_file = api.path.checkout_dir / '.gclient'
  src_file = api.path.split(api.path.checkout_dir)[0].join('.gclient')
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
  """Check if the .gclient file is acceptable to use for the selected builder

  Args:
      api: Recipe API object.
  Returns:
      A string that represents the error or an empty string when there is no
      warning
  """
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

  # Are there any Chrome/Chromium builders with >1 gclient solution?
  if len(api.gclient.c.solutions) != 1:  # pragma: no cover
    raise api.step.StepFailure(
        'Unsupported UTR invocation for this builder. Please file a general '
        "infra bug via https://g.co/bugatrooper if you're seeing this, and "
        'provide your full cmd-line invocation.')

  builder_cfg = api.gclient.c.solutions[0]
  for builder_custom_var, builder_val in builder_cfg.custom_vars.items():
    assignment_snippet = f'`"{builder_custom_var}": "{builder_val}"`'
    if (builder_custom_var not in current_custom_vars or
        builder_val != str(current_custom_vars[builder_custom_var])):
      mismatch_messages.append(
          f'- custom_var {builder_custom_var} has mismatched value in the '
          f'local .gclient file. Set it to: {assignment_snippet}')

  current_target_os = gclient_config.get('target_os', [])
  builder_target_os = api.gclient.c.target_os
  missing_os = list(set(builder_target_os) - set(current_target_os))
  target_os_snippet = (
      '`target_os = [%s]`' %
      ', '.join(f'"{os}"' for os in current_target_os + missing_os))
  if missing_os:
    mismatch_messages.append(
        f'- target_os in builder config `"{missing_os}"` is not in the local '
        f'.gclient file. Set it to: {target_os_snippet}')

  # TODO(crbug.com/41492686): Check custom_deps
  error_info = ''
  if mismatch_messages:
    error_info = ('Caution: your .gclient file and the builder\'s mismatches in'
                  ' the following way(s). Please run "gclient sync" after '
                  'resolving these:\n' + '\n'.join(mismatch_messages))
  return error_info


def check_gn_args(api: RecipeApi, build_dir: Path,
                  builder_id: chromium.BuilderId) -> str:
  """Check if the args.gn file is acceptable to use for the selected builder

  Args:
      api: Recipe API object.
      build_dir: Path to the build dir being used
      builder_id: The BuilderId for the builder being run
  Returns:
      A string that represents the error or an empty string when there is no
      warning
  """

  def get_imports(args):
    return [l.strip() for l in args.splitlines() if l.startswith('import(')]

  builder_gn_args = api.chromium.mb_lookup(
      builder_id, recursive=False, name='lookup_builder_gn_args')
  builder_imports = get_imports(builder_gn_args)
  builder_gn_args = api.gn.parse_gn_args(builder_gn_args)

  current_gn_args, _ = api.gn.read_args(build_dir)
  current_imports = get_imports(current_gn_args)
  current_gn_args = api.gn.parse_gn_args(current_gn_args)

  mismatch_messages = []
  for arg in current_gn_args:
    if arg not in builder_gn_args:
      mismatch_messages.append(
          f'- `{arg}` in current build dir is not set by the builder')
    elif builder_gn_args[arg] != current_gn_args[arg]:
      mismatch_messages.append(
          f'- `{arg}` in current build (`{current_gn_args[arg]}`) does not match '
          f'builder value (`{builder_gn_args[arg]}`)')
  for arg in builder_gn_args:
    if arg not in current_gn_args:
      mismatch_messages.append(
          f'- `{arg}` set by the builder is absent in the current build dir')

  for missing_import in set(builder_imports) - set(current_imports):
    mismatch_messages.append(
        f'- `{missing_import}` used by the builder is absent in the '
        'current build dir')
  for missing_import in set(current_imports) - set(builder_imports):
    mismatch_messages.append(
        f'- `{missing_import}` in current build dir is not used by the '
        'builder')

  error_info = ''
  if mismatch_messages:
    error_info = ('Caution: your build\'s gn args and the builder\'s '
                  'mismatches in the following way(s):\n' +
                  '\n'.join(mismatch_messages))
  return error_info


def check_upstream_branch(api: RecipeApi):
  """Check if the current branch has an upstream branch for diffing against

  Args:
      api: Recipe API object.
  Returns:
      A string that represents the error or an empty string when there is no
      warning
  """
  if get_upstream_branch(api).retcode != 0:
    return 'Caution: failed to get an upstream branch from the current checkout'


def get_upstream_branch(api: RecipeApi):
  return api.git(
      'rev-parse',
      '--abbrev-ref',
      '--symbolic-full-name',
      '@{u}',
      name='check upstream branch',
      stdout=api.raw_io.output(),
      raise_on_failure=False,
      step_test_data=lambda: api.raw_io.test_api.stream_output('origin/main\n'))


def create_tests(
    api: RecipeApi,
    properties: InputProperties,
    build_dir: Path,
    got_revisions: Mapping[str, str],
    builder_id: chromium.BuilderId,
    builder_config: ctbc.BuilderConfig,
    should_build: bool,
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
  """
  test_names = properties.test_names
  preserve_gn_args = properties.rerun_options.preserve_gn_args
  builder_recipe = properties.builder_recipe
  targets_config = api.chromium_tests.create_targets_config(
      builder_config,
      got_revisions,
      api.path.checkout_dir,
      targets_spec_dir=api.path.checkout_dir.joinpath('testing', 'buildbot'))

  def _get_matching_test(requested_test_name):
    for t in targets_config.all_tests:
      if requested_test_name in (t.name, t.canonical_name):
        return t
    raise api.step.StepFailure(
        f'No suites on the bot matched the request for {requested_test_name}')

  tests = [_get_matching_test(n) for n in test_names]

  # TODO(crbug.com/335017001): Disable 'layout tests' archiving since we run
  # ci builders that would point to gcs dirs that devs do not have access to.
  # Remove when layout tests can be archived
  for test in tests:
    if isinstance(test, SwarmingIsolatedScriptTest):
      test.spec = attr.evolve(test.spec, results_handler_name=None)
  if should_build:
    raw_result, preserve_gn_args = compile_targets(api, properties, tests,
                                                   builder_id, preserve_gn_args,
                                                   build_dir, builder_recipe)
    if raw_result.status != common_pb2.SUCCESS:
      return raw_result, None
  skylab_tests = [test for test in tests if test.is_skylabtest]
  if not should_build or preserve_gn_args or skylab_tests:
    # When compiling, "mb.py gen" will produce the *.isolate files for us. In
    # all other instances, we need to ask mb.py to do so specifically. Do so
    # for *all* possible targets. This shouldn't take much longer, and
    # simplifies things a bit.
    api.chromium.mb_isolate_everything(None, build_dir=build_dir)

  isolate_tests = [test for test in tests if test.isolate_target]
  if isolate_tests:
    api.chromium_tests.isolate_tests(
        builder_config, isolate_tests, '', '', build_dir=build_dir)

  # TODO(crbug.com/41492686): Prepare skylab artifacts
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
  api.path.checkout_dir = api.path.abs_to_path(checkout_dir)
  api.chromium_checkout.checkout_dir = api.path.cache_dir

  if OLD_PATH_TYPE:  # pragma: no cover
    # see comment in import block at top of this file.
    build_dir = build_dir or api.path.join(api.path.checkout_dir, 'out',
                                           api.chromium.c.build_config_fs)
    build_path = Path(RootBasePath(), build_dir)
  else:  # pragma: no cover
    if build_dir:
      build_path = api.path.cast_to_path(build_dir)
    else:
      build_path = api.path.checkout_dir.joinpath(
          'out', api.chromium.c.build_config_fs)

  api.file.ensure_directory('ensure_build_dir', build_path)

  api.chromium.output_dir = build_path
  return (compiling_builder_id, compiling_builder_config, api.path.checkout_dir,
          build_path)


def generate_got_revisions_map(api):
  """Generates a minimalistic got_revisions mapping.

  got_revisions is a dict normally returned by gclient/bot_update recipe
  modules and subsequently referenced throughout the Chromium recipe. Since the
  UTR runs on developer workstations, we want to avoid modifying or mucking
  about with the local checkout as much as we can. To that end, this returns a
  very basic got_revisions map that includes only the keys that normally point
  to the chromium/src.git revision.

  The rev these keys point to is the local HEAD. Note that if the checkout
  contains any local commits, this rev will be unique to the checkout.
  """
  result = api.git('rev-parse', 'HEAD', stdout=api.raw_io.output())
  rev = result.stdout.decode('utf-8').strip()
  return {
      # See the substitutions in recipe_modules/chromium_tests/generators.py
      # for what got_* revision keys might be used.
      'got_cr_revision': rev,
      'got_revision': rev,
      'got_src_revision': rev,
  }

def compile_targets(
    api: RecipeApi,
    properties: InputProperties,
    tests: Iterable[Test],
    builder_id: chromium.BuilderId,
    preserve_gn_args: bool,
    build_dir: str,
    builder_recipe: str,
) -> result_pb2.RawResult:
  """Builds the test targets

  Args:
      api: Recipe API object.
      properties: InputProperties given to the recipe
      tests: Iterable of test objects to be compiled
      builder_id: The ID of the builder to compile for
      preserve_gn_args: Bool whether to have the recipe overwrite the gn args
        with the builder_id's gn args
      build_dir: Path to the directory to use for building
      builder_recipe: The recipe normally run by the requested builder
  """
  targets = list(itertools.chain(*[t.compile_targets() for t in tests]))

  # Remove duplicate targets.
  targets = sorted(set(targets))

  # Only recipes that support try should handle changed files
  if (api.code_coverage.using_coverage and
      builder_recipe in ('chromium/orchestrator', 'chromium_trybot')):
    preserve_gn_args = handle_code_coverage(api, build_dir, properties,
                                            builder_id)

  if preserve_gn_args and api.path.exists(build_dir.join('args.gn')):
    api.gn.gen(build_dir, 'gn_gen')
  else:
    tests_to_isolate = [t.isolate_target for t in tests if t.isolate_target]
    api.chromium.mb_gen(
        builder_id,
        name='generate_build_files',
        recursive_lookup=True,
        build_dir=build_dir,
        isolated_targets=tests_to_isolate)

  use_reclient = get_remote_compile_options(api, build_dir)

  if use_reclient:
    api.reclient.experimental_credentials_helper = 'luci-auth'
    api.reclient.experimental_credentials_helper_args = ' '.join([
        'token',
        '-scopes-context',
        '-json-output=-',
        '-json-format=reclient',
        '-lifetime=5m',
    ])
  return api.chromium.compile(
      targets,
      skip_log_upload=True,
      target_output_dir=str(build_dir),
      use_reclient=use_reclient), preserve_gn_args


def handle_code_coverage(
    api: RecipeApi,
    build_dir: Path,
    properties: InputProperties,
    builder_id: chromium.BuilderId,
) -> bool:
  """Handles code coverage for runs using try builders

  Based on the input properties (bypass_branch_check and skip_instrumentation)
  this will either:
    - Instrument everything by removing the coverage_instrumentation_input_file
    when bypass_branch_check is true and skip_instrumentation is false
    - Instrument nothing by creating an empty instrumentation file when
    bypass_branch_check is true and skip_instrumentation is true
    - Instrument only the changed files when bypass_branch_check is false

  Args:
      api: Recipe API object.
      build_dir: Path to the directory to use for building
      properties: InputProperties given to the recipe
      builder_id: The ID of the builder to get tests from
  Returns:
      A boolean for whether or not the gn args need to be preserved or the
      builder's gn args can overwrite them
  """
  # If we're bypassing the branch check and not skipping instrumentation
  # then remove the gn arg to instrument everything
  if (properties.rerun_options.bypass_branch_check and
      not properties.rerun_options.skip_instrumentation):
    if (not properties.rerun_options.preserve_gn_args or
        not api.path.exists(build_dir.join('args.gn'))):
      gn_args = api.chromium.mb_lookup(
          builder_id,
          recursive=False,
          name='lookup_builder_gn_args_for_code_coverage')
    else:
      gn_args, _ = api.gn.read_args(build_dir)
    api.file.write_text(
        'remove coverage_instrumentation_input_file gn arg',
        build_dir.joinpath('args.gn'), '\n'.join(
            arg for arg in gn_args.split('\n')
            if not arg.startswith('coverage_instrumentation_input_file')))
    return True
  paths = []
  if not properties.rerun_options.skip_instrumentation:
    with api.context(cwd=api.path.checkout_dir):
      step_result = get_upstream_branch(api)
      branch_upstream_name = step_result.stdout.decode('utf-8').strip()
      step_result = api.git(
          '-c',
          'core.quotePath=false',
          'diff',
          '--merge-base',
          '--name-only',
          branch_upstream_name,
          name='git diff to instrument',
          stdout=api.raw_io.output(),
          step_test_data=lambda: api.raw_io.test_api.stream_output('foo.cc'))
    paths = [p.decode('utf-8') for p in step_result.stdout.splitlines()]
    paths.sort()
    if api.platform.is_win:
      paths = [path.replace('\\', '/') for path in paths]
  api.code_coverage.src_dir = api.chromium_checkout.src_dir
  api.code_coverage.instrument(paths)
  return properties.rerun_options.preserve_gn_args


def get_remote_compile_options(api, build_dir) -> bool:
  use_reclient = False
  if api.chromium.c.project_generator.tool == 'mb':
    gn_args, _ = api.gn.read_args(build_dir)
    args = api.gn.parse_gn_args(gn_args)
    use_reclient = args.get('use_remoteexec') == 'true'
  return use_reclient


def create_rerun_result(api: RecipeApi, \
                        rerun_options: list[tuple[str, InputProperties.RerunOptions]],
                        info: str,
                        output_properties_file: str) -> result_pb2.RawResult:
  """Create a result for retriggering the recipe

  Writes the provided rerun options and creates a RawResult meant for the CLI to
  retrigger the recipe. The presence of rerun_properties should indicate to the
  CLI that the recipe can be invoked again differently for a different result.
  The info will be included in the RawResult meant to provide additional
  information to the user (e.g. if gclient args from the builder are not set
  locally).

  Args:
      api: Recipe API object.
      rerun_options: A list of strings and input properties. These strings
        will be presented to the user as options and the corresponding input
        properties fed back to the recipe when selected. An entry with empty
        input properties will signal the recipe should not be reinvoked for that
        prompt selection.
      info: Information string that the user should see
      output_properties_file: Where the rerun_options should be written
  Returns:
    A RawResult that should be returned to trigger a rerun
  """
  if output_properties_file:
    output = []
    for prompt, properties in rerun_options:
      output.append((prompt,
                     json_format.MessageToDict(
                         message=properties, preserving_proto_field_name=True)))
    api.file.write_json(
        f'write output_properties_file {output_properties_file}',
        output_properties_file, output)
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

  def boilerplate_properties(
      test_names=None,
      checkout_path='[CACHE]/src',
      run_type=InputProperties.RunType.RUN_TYPE_COMPILE_AND_RUN,
      preserve_gn_args=False,
      build_dir=None,
      bypass_gclient=True,
      bypass_gn_args=True,
      builder_recipe='chromium',
      output_properties_file='checkout/output_properties.json',
      bypass_branch_check=False,
      skip_instrumentation=False,
  ):
    if not test_names:
      test_names = ['browser_tests']
    return api.properties(
        test_names=test_names,
        checkout_path=checkout_path,
        run_type=run_type,
        build_dir=build_dir,
        builder_recipe=builder_recipe,
        output_properties_file=output_properties_file,
        rerun_options=InputProperties.RerunOptions(
            bypass_gclient=bypass_gclient,
            bypass_gn_args=bypass_gn_args,
            preserve_gn_args=preserve_gn_args,
            bypass_branch_check=bypass_branch_check,
            skip_instrumentation=skip_instrumentation,
        ))

  def boilerplate(
      target_spec=None,
      build=None,
      **kwargs,
  ):
    return sum([
        boilerplate_properties(**kwargs),
        ctbc_properties(
            builder_spec=ctbc.BuilderSpec.create(
                gclient_config='chromium',
                chromium_config='chromium',
            )),
        api.chromium_tests.read_targets_spec(
            'fake-group', target_spec or {
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
        build or api.chromium.generic_build(
            builder_group='fake-group',
            builder='fake-tester',
        ),
    ], api.empty_test_data())

  yield api.test(
      'basic',
      boilerplate(
          preserve_gn_args=False,
          bypass_gclient=False,
          target_spec={
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
              }
          },
      ),
      api.path.exists(api.path.cache_dir / '.gclient'),
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
      api.post_process(post_process.MustRun, 'isolate tests'),
      api.post_process(post_process.MustRun, 'lookup GN args'),
      api.post_process(post_process.MustRun,
                       'test_pre_run.[trigger] browser_tests'),
      api.post_process(post_process.StepCommandContains, 'compile',
                       ['[CACHE]/src/out/Release', 'browser_tests']),
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
      'SwarmingIsolatedScriptTest_skips_upload',
      boilerplate(
          test_names=['fake-script-test'],
          target_spec={
              'fake-tester': {
                  'isolated_scripts': [{
                      'name': 'fake-script-test',
                      'script': 'fake-script',
                      'results_handler': 'layout tests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                          },
                      }
                  }],
              }
          },
      ),
      api.post_process(post_process.DoesNotRun,
                       'archive results for fake-script-test'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'child_tester',
      boilerplate(
          build=api.chromium_tests_builder_config.ci_build(
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
              }))),
      api.post_process(post_process.MustRun, 'browser_tests'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'unsupported_child_testers',
      boilerplate_properties(),
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
      api.post_process(
          post_process.SummaryMarkdownRE,
          'Unsupported UTR invocation for builder fake-grandchild-tester.*',
      ),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'no_tests',
      boilerplate(test_names=['non_existant_test']),
      api.post_process(
          post_process.SummaryMarkdown,
          'No suites on the bot matched the request for non_existant_test',
      ),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'multiple_tests',
      boilerplate(
          test_names=['browser_tests', 'unit_tests'],
          target_spec={
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
                      'name': 'unit_tests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                              'pool': 'fake-pool',
                          },
                      },
                  }],
              },
          }),
      api.post_process(post_process.MustRun, 'compile'),
      api.post_process(post_process.MustRun, 'isolate tests'),
      api.post_process(post_process.MustRun,
                       'test_pre_run.[trigger] browser_tests'),
      api.post_process(post_process.MustRun,
                       'test_pre_run.[trigger] unit_tests'),
      api.post_process(post_process.StepCommandContains, 'generate_build_files',
                       ['-m', 'fake-group', '-b', 'fake-tester']),
      api.post_process(post_process.MustRun, 'browser_tests'),
      api.post_process(post_process.MustRun, 'unit_tests'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'code_coverage',
      boilerplate(
          checkout_path='[CACHE]\\src',
          build_dir='[CACHE]\\src\\out\\Release',
          builder_recipe='chromium_trybot',
          build=api.chromium.generic_build(
              builder_group='fake-group',
              builder='fake-tester',
              bucket='try',
          ),
      ),
      api.platform('win', 32),
      api.code_coverage(use_clang_coverage=True),
      api.post_process(post_process.MustRun, 'lookup GN args'),
      api.step_data(
          'lookup GN args',
          stdout=api.raw_io.output_text(
              'coverage_instrumentation_input_file = "files_to_instrument.txt"')
      ),
      api.post_process(post_process.MustRun, 'save paths of affected files'),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'code_coverage_no_upstream',
      ctbc_properties(),
      boilerplate_properties(
          checkout_path='[CACHE]\\src',
          build_dir='[CACHE]\\src\\out\\Release',
          builder_recipe='chromium_trybot',
          output_properties_file='[CACHE]\\out.json'),
      api.chromium.generic_build(
          builder_group='fake-group',
          builder='fake-tester',
      ),
      api.platform('win', 32),
      api.code_coverage(use_clang_coverage=True),
      api.step_data(
          'check upstream branch',
          retcode=1,
      ),
      api.post_process(
          post_process.ResultReason,
          'Caution: failed to get an upstream branch from the current checkout'
      ),
      api.post_process(post_process.MustRun,
                       'write output_properties_file [CACHE]\\out.json'),
      api.post_process(
          post_process.StepCommandContains,
          'write output_properties_file [CACHE]\\out.json', [
              '[["instrument everything", '
              '{"bypass_branch_check": true, "bypass_gclient": true, '
              '"bypass_gn_args": true}], '
              '["skip instrumentation", {"bypass_branch_check": true, '
              '"bypass_gclient": true, "bypass_gn_args": true, '
              '"skip_instrumentation": true}], '
              '["abort", {}]]'
          ]),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'code_coverage_skip_instrument',
      boilerplate(
          checkout_path='[CACHE]\\src',
          build_dir='[CACHE]\\src\\out\\Release',
          builder_recipe='chromium_trybot',
          build=api.chromium.generic_build(
              builder_group='fake-group',
              builder='fake-tester',
              bucket='try',
          ),
          bypass_branch_check=True,
          skip_instrumentation=True,
      ),
      api.platform('win', 32),
      api.code_coverage(use_clang_coverage=True),
      api.post_process(post_process.MustRun, 'lookup GN args'),
      api.step_data(
          'lookup GN args',
          stdout=api.raw_io.output_text(
              'coverage_instrumentation_input_file = "files_to_instrument.txt"')
      ),
      api.post_process(post_process.MustRun, 'save paths of affected files'),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'code_coverage_instrument_everything_user_gn_args',
      boilerplate(
          checkout_path='[CACHE]\\src',
          build_dir='[CACHE]\\src\\out\\Release',
          builder_recipe='chromium_trybot',
          build=api.chromium.generic_build(
              builder_group='fake-group',
              builder='fake-tester',
              bucket='try',
          ),
          bypass_branch_check=True,
          skip_instrumentation=False,
          preserve_gn_args=True,
      ),
      api.platform('win', 32),
      api.code_coverage(use_clang_coverage=True),
      api.path.exists(
          api.path.cache_dir.join('src', 'out', 'Release', 'args.gn')),
      api.step_data(
          'read GN args',
          api.raw_io.output_text('coverage_instrumentation_input_file = '
                                 '".code-coverage/files_to_instrument.txt"\n'
                                 'use_remoteexec = true')),
      api.post_process(post_process.MustRun,
                       'remove coverage_instrumentation_input_file gn arg'),
      api.post_process(post_process.StepCommandContains,
                       'remove coverage_instrumentation_input_file gn arg',
                       ['use_remoteexec = true']),
      api.post_process(post_process.StepCommandDoesNotContain,
                       'remove coverage_instrumentation_input_file gn arg',
                       ['coverage_instrumentation_input_file']),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'code_coverage_instrument_everything_builder_gn_args',
      boilerplate(
          checkout_path='[CACHE]\\src',
          build_dir='[CACHE]\\src\\out\\Release',
          builder_recipe='chromium_trybot',
          build=api.chromium.generic_build(
              builder_group='fake-group',
              builder='fake-tester',
              bucket='try',
          ),
          bypass_branch_check=True,
          skip_instrumentation=False,
          preserve_gn_args=False,
      ),
      api.platform('win', 32),
      api.code_coverage(use_clang_coverage=True),
      api.step_data(
          'read GN args',
          api.raw_io.output_text('coverage_instrumentation_input_file = '
                                 '".code-coverage/files_to_instrument.txt"\n'
                                 'b = true')),
      api.step_data(
          'lookup_builder_gn_args_for_code_coverage',
          stdout=api.raw_io.output_text(
              'coverage_instrumentation_input_file = '
              '".code-coverage/files_to_instrument.txt"\n'
              'b = true')),
      api.post_process(post_process.MustRun,
                       'remove coverage_instrumentation_input_file gn arg'),
      api.post_process(post_process.StepCommandContains,
                       'remove coverage_instrumentation_input_file gn arg',
                       ['b = true']),
      api.post_process(post_process.StepCommandDoesNotContain,
                       'remove coverage_instrumentation_input_file gn arg',
                       ['coverage_instrumentation_input_file']),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'reclient',
      boilerplate(),
      api.step_data('read GN args',
                    api.raw_io.output_text('use_remoteexec = true')),
      api.post_process(post_process.MustRun, 'isolate tests'),
      api.post_process(post_process.MustRun, 'lookup GN args'),
      api.post_process(post_process.MustRun,
                       'test_pre_run.[trigger] browser_tests'),
      api.post_process(post_process.StepCommandContains, 'compile', [
          '[CACHE]/src/third_party/ninja/ninja', '-C',
          '[CACHE]/src/out/Release', '-j', '160', 'browser_tests'
      ]),
      api.post_process(post_process.MustRun, 'browser_tests'),
      api.post_process(
          post_process.MustRun,
          'postprocess for reclient.shutdown reproxy via bootstrap'),
      api.post_process(post_process.DoesNotRun,
                       'postprocess for reclient.stop cloudtail'),
      api.post_process(post_process.DoesNotRun,
                       'preprocess for reclient.start cloudtail: reproxy.INFO'),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'siso',
      boilerplate(),
      api.properties(
          **{
              '$build/siso': {
                  'configs': ['builder'],
                  'enable_cloud_profiler': True,
                  'enable_cloud_trace': True,
                  'experiments': [],
                  'project': 'rbe-chromium-untrusted'
              },
          }),
      api.step_data(
          'read GN args',
          api.raw_io.output_text('use_remoteexec = true\nuse_siso: true')),
      api.post_process(post_process.MustRun, 'isolate tests'),
      api.post_process(post_process.MustRun, 'lookup GN args'),
      api.post_process(post_process.MustRun,
                       'test_pre_run.[trigger] browser_tests'),
      api.post_process(post_process.StepCommandContains, 'compile', [
          '[CACHE]/src/third_party/siso/siso',
          'ninja',
      ]),
      api.post_process(post_process.StepCommandDoesNotContain, 'compile', [
          '--enable_cloud_logging',
      ]),
      api.post_process(post_process.MustRun, 'browser_tests'),
      api.post_process(post_process.DoesNotRun,
                       'postprocess for reclient.stop cloudtail'),
      api.post_process(post_process.DoesNotRun,
                       'preprocess for reclient.start cloudtail: reproxy.INFO'),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'bad_gclient',
      boilerplate_properties(
          preserve_gn_args=False,
          bypass_gclient=False,
      ),
      ctbc_properties(
          builder_spec=ctbc.BuilderSpec.create(
              gclient_config='ios',
              chromium_config='chromium',
          )),
      api.chromium.generic_build(
          builder_group='fake-group',
          builder='fake-tester',
      ),
      api.path.exists(api.path.cache_dir.joinpath('src', '.gclient')),
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
          'following way(s). Please run "gclient sync" after resolving these:\n'
          '- rbe_instance has been set in the .gclient file\n'
          '- custom_var checkout_telemetry_dependencies has mismatched value '
          'in the local .gclient file. Set it to: '
          '`"checkout_telemetry_dependencies": "True"`\n'
          '- target_os in builder config `"[\'ios\']"` is not in the local '
          '.gclient file. Set it to: `target_os = ["os", "ios"]`'),
      api.post_process(post_process.StepCommandContains, 'read gclient',
                       ['[CACHE]/src/.gclient']),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'gclient_above_src',
      boilerplate(
          preserve_gn_args=False,
          bypass_gclient=False,
      ),
      api.path.exists(api.path.cache_dir / '.gclient'),
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
      boilerplate_properties(bypass_gclient=False),
      ctbc_properties(
          builder_spec=ctbc.BuilderSpec.create(
              gclient_config='ios',
              chromium_config='chromium',
          )),
      api.chromium.generic_build(
          builder_group='fake-group',
          builder='fake-tester',
      ),
      api.path.exists(api.path.cache_dir.joinpath('src', '.gclient')),
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
      boilerplate_properties(bypass_gclient=False),
      ctbc_properties(
          builder_spec=ctbc.BuilderSpec.create(
              gclient_config='chromium',
              chromium_config='chromium',
          )),
      api.chromium.generic_build(
          builder_group='fake-group',
          builder='fake-tester',
      ),
      api.expect_exception('FileNotFoundError'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'bad_gn_args',
      boilerplate_properties(
          bypass_gn_args=False, build_dir='[CACHE]/src/out/Release'),
      ctbc_properties(
          builder_spec=ctbc.BuilderSpec.create(
              gclient_config='chromium',
              chromium_config='chromium',
          )),
      api.chromium.generic_build(
          builder_group='fake-group',
          builder='fake-tester',
      ),
      api.path.exists(api.path.cache_dir / 'src/out/Release/args.gn'),
      api.step_data(
          'lookup_builder_gn_args',
          stdout=api.raw_io.output_text('import("//builder.args")\n'
                                        'a = "1"\n'
                                        'c = true\n'
                                        'd = true')),
      api.step_data(
          'read GN args',
          api.raw_io.output_text('import("//local.args")\n'
                                 'b="2"\n'
                                 'c=false\n'
                                 'd=true')),
      api.post_process(
          post_process.ResultReason,
          'Caution: your build\'s gn args and the builder\'s mismatches in the '
          'following way(s):\n'
          '- `b` in current build dir is not set by the builder\n'
          '- `c` in current build (`false`) does not match builder value '
          '(`true`)\n'
          '- `a` set by the builder is absent in the current build dir\n'
          '- `import("//builder.args")` used by the builder is absent in the '
          'current build dir\n'
          '- `import("//local.args")` in current build dir is not used by the '
          'builder'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'changed_build_dir',
      boilerplate(build_dir='/fake_root/fake_out/Debug'),
      api.post_process(post_process.MustRun, 'compile'),
      api.post_process(post_process.MustRun, 'isolate tests'),
      api.post_process(post_process.MustRun, 'lookup GN args'),
      api.post_process(post_process.MustRun,
                       'test_pre_run.[trigger] browser_tests'),
      api.post_process(post_process.StepCommandContains, 'compile',
                       ['/fake_root/fake_out/Debug', 'browser_tests']),
      api.post_process(
          post_process.StepCommandContains, 'isolate tests',
          ['/fake_root/fake_out/Debug/browser_tests.isolated.gen.json']),
      api.post_process(post_process.StepCommandContains, 'find command lines',
                       ['/fake_root/fake_out/Debug']),
      api.post_process(post_process.MustRun, 'browser_tests'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'no_compile_targets',
      boilerplate(),
      api.post_process(post_process.MustRun, 'compile'),
      api.post_process(post_process.MustRun, 'isolate tests'),
      api.post_process(post_process.MustRun,
                       'test_pre_run.[trigger] browser_tests'),
      api.post_process(post_process.StepCommandContains, 'generate_build_files',
                       ['-m', 'fake-group', '-b', 'fake-tester']),
      api.post_process(post_process.MustRun, 'browser_tests'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'failed_test',
      boilerplate(),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', '', failures=['Test.One']),
      api.post_process(post_process.MustRun, 'compile'),
      api.post_process(post_process.MustRun, 'isolate tests'),
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
      boilerplate(),
      api.override_step_data('compile', retcode=1),
      api.post_process(post_process.MustRun, 'compile'),
      api.post_process(post_process.DoesNotRun,
                       'test_pre_run.[trigger] browser_tests'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'preserve_gn_args',
      boilerplate(preserve_gn_args=True),
      api.path.exists(
          api.path.cache_dir.join('src', 'out', 'Release', 'args.gn')),
      api.post_process(post_process.MustRun, 'compile'),
      api.post_process(post_process.MustRun, 'generate .isolate files'),
      api.post_process(post_process.MustRun, 'isolate tests'),
      api.post_process(post_process.MustRun,
                       'test_pre_run.[trigger] browser_tests'),
      api.post_process(post_process.MustRun, 'gn_gen'),
      api.post_process(post_process.DoesNotRun, 'generate_build_files'),
      api.post_process(post_process.DoesNotRun, 'lookup GN args'),
      api.post_process(post_process.MustRun, 'browser_tests'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'no_args_gn_uses_builder',
      boilerplate(bypass_gn_args=False, preserve_gn_args=True),
      api.post_process(post_process.MustRun, 'generate_build_files'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'skip_build',
      boilerplate(run_type=InputProperties.RunType.RUN_TYPE_RUN),
      api.post_process(post_process.DoesNotRun, 'compile'),
      api.post_process(post_process.DoesNotRun, 'generate_build_files'),
      api.post_process(post_process.MustRun, 'isolate tests'),
      api.post_process(post_process.MustRun,
                       'test_pre_run.[trigger] browser_tests'),
      api.post_process(post_process.MustRun, 'browser_tests'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'skip_test',
      boilerplate(run_type=InputProperties.RunType.RUN_TYPE_COMPILE),
      api.post_process(post_process.MustRun, 'compile'),
      api.post_process(post_process.MustRun, 'isolate tests'),
      api.post_process(post_process.StepCommandContains, 'generate_build_files',
                       ['-m', 'fake-group', '-b', 'fake-tester']),
      api.post_process(post_process.DoesNotRun,
                       'test_pre_run.[trigger] browser_tests'),
      api.post_process(post_process.DoesNotRun, 'browser_tests'),
      api.post_process(post_process.DropExpectation),
  )
