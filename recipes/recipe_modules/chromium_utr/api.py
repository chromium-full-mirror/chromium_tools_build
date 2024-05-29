# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Shareable implementation of the recipe side of Chromium's UTR."""

import attr
import copy
import itertools
from collections.abc import Iterable, Mapping
from google.protobuf import json_format
from google.protobuf.json_format import MessageToDict

from recipe_engine import recipe_api
from recipe_engine.config_types import Path

from PB.go.chromium.org.luci.buildbucket.proto import (
    common as common_pb2,
    builds_service as builds_service_pb2,
)
from PB.recipe_engine import result as result_pb2
from PB.recipe_modules.build.chromium_utr.request import Request

from RECIPE_MODULES.build import chromium
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc
from RECIPE_MODULES.build.chromium_tests_builder_config import (
    builder_config as builder_config_module)
from RECIPE_MODULES.build.chromium_tests.steps import (
    Test, SwarmingIsolatedScriptTest)

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


class ChromiumUTRApi(recipe_api.RecipeApi):

  def run(self, properties: Request, builder_id: chromium.BuilderId,
          builder_config: ctbc.BuilderConfig) -> result_pb2.RawResult:
    """Compiles and runs tests as needed.

    Args:
      properties: Request given to the recipe
      compiling_builder_id: BuilderId for the compiler builder
      compiling_builder_config: BuilderConfig for the compiling builder

    Returns:
      result_pb2.RawResult of the recipe execution
    """
    should_build = properties.run_type != Request.RunType.RUN_TYPE_RUN
    should_test = properties.run_type != Request.RunType.RUN_TYPE_COMPILE

    compiling_builder_id, compiling_builder_config = self.get_compiling_builder_config(
        builder_id, builder_config)

    build_path = self.configure_build_dir(properties.build_dir)
    result = self.prerun_checks(properties, build_path, compiling_builder_id)
    if result != None:
      return result

    got_revisions = self.generate_got_revisions_map()

    raw_result, tests = self.create_tests(properties, build_path, got_revisions,
                                          compiling_builder_id,
                                          compiling_builder_config,
                                          should_build)
    if raw_result and raw_result.status != common_pb2.SUCCESS:
      return raw_result
    if not should_test:
      return result_pb2.RawResult(status=common_pb2.SUCCESS)

    test_runner = self.m.chromium_tests.create_test_runner(tests)
    with self.m.chromium_tests.wrap_chromium_tests(tests):
      self.m.chromium_tests.configure_swarming(True)
      # Lower pri for faster turn-around time in debugging. The UTR shouldn't
      # get so much use that it affects CI/CQ traffic substantially. But we can
      # check for sure using the UTR-specific tag below, and reassess if needed.
      self.m.chromium_swarming.default_priority = 20
      self.m.chromium_swarming.add_default_tag('is_utr:1')
      return test_runner()

  def get_compiling_builder_config(
      self, builder_id: chromium.BuilderId, builder_config: ctbc.BuilderConfig
  ) -> tuple[chromium.BuilderId, ctbc.BuilderConfig]:
    """Gets the config of the compiling-builder for the given BuilderConfig

    Essentially just returns the BuilderConfig of the parent builder if the
    given BuilderConfig is a child-tester.

    Args:
      builder_id: BuilderId of the given builder
      builder_config: BuilderConfig of the given builder

    Returns:
      Tuple of BuilderId for the compiler builder, BuilderConfig for the
      compiling builder
    """
    if builder_config.execution_mode != ctbc.TEST:
      return builder_id, builder_config

    compiling_builder_id = chromium.BuilderId.create_for_group(
        builder_config.parent_builder_group, builder_config.parent_buildername)
    compiling_builder_config = builder_config_module.BuilderConfig.lookup(
        compiling_builder_id, builder_config.builder_db)
    if compiling_builder_config.execution_mode != ctbc.COMPILE_AND_TEST:
      raise self.m.step.StepFailure(
          f'Unsupported UTR invocation for builder {builder_id} triggered by '
          f'builder {builder_config.parent_buildername}. Please file a '
          "general infra bug via https://g.co/bugatrooper if you're seeing "
          'this.')
    return compiling_builder_id, compiling_builder_config

  def configure_build_dir(self, build_dir):
    if OLD_PATH_TYPE:  # pragma: no cover
      # see comment in import block at top of this file.
      build_dir = build_dir or self.m.path.join(
          self.m.path.checkout_dir, 'out', self.m.chromium.c.build_config_fs)
      build_path = Path(RootBasePath(), build_dir)
    else:  # pragma: no cover
      if build_dir:
        build_path = self.m.path.cast_to_path(build_dir)
      else:
        build_path = self.m.path.checkout_dir.joinpath(
            'out', self.m.chromium.c.build_config_fs)

    self.m.file.ensure_directory('ensure_build_dir', build_path)
    self.m.chromium.output_dir = build_path
    return build_path

  def get_gclient_config(self):
    src_file = self.m.path.split(
        self.m.path.checkout_dir)[0].joinpath('.gclient')
    if self.m.path.exists(src_file):
      gclient_file_path = src_file
    else:
      raise FileNotFoundError(f'.gclient file not found at {str(src_file)}')
    # TODO(https://crbug.com/327270127): Use some utility to get the current
    # .gclient config so we don't have to exec() the file
    gclient_text = self.m.file.read_text('read gclient', gclient_file_path)
    local_env = {}
    exec(gclient_text, {}, local_env)
    return local_env

  def check_gclient(self) -> str:
    """Check if the .gclient file is acceptable to use for the selected builder

    Returns:
        A string that represents the error or an empty string when there is no
        warning
    """
    mismatch_messages = []
    gclient_config = self.get_gclient_config()
    solution = [
        sol for sol in gclient_config.get('solutions', []) if sol.get(
            'url', '') == 'https://chromium.googlesource.com/chromium/src.git'
    ]
    if len(solution) != 1:
      return ('Caution: your .gclient file could not be validated. Exactly one '
              'solution with \'url\' set to '
              'https://chromium.googlesource.com/chromium/src.git'
              ' must be set\n')
    solution = solution[0]

    current_custom_vars = solution.get('custom_vars', {})
    if 'rbe_instance' in current_custom_vars:
      mismatch_messages.append(
          '- rbe_instance has been set in the .gclient file')

    # Are there any Chrome/Chromium builders with >1 gclient solution?
    if len(self.m.gclient.c.solutions) != 1:  # pragma: no cover
      raise self.m.step.StepFailure(
          'Unsupported UTR invocation for this builder. Please file a general '
          "infra bug via https://g.co/bugatrooper if you're seeing this, and "
          'provide your full cmd-line invocation.')

    builder_cfg = self.m.gclient.c.solutions[0]
    for builder_custom_var, builder_val in builder_cfg.custom_vars.items():
      assignment_snippet = f'`"{builder_custom_var}": "{builder_val}"`'
      if (builder_custom_var not in current_custom_vars or
          builder_val != str(current_custom_vars[builder_custom_var])):
        mismatch_messages.append(
            f'- custom_var {builder_custom_var} has mismatched value in the '
            f'local .gclient file. Set it to: {assignment_snippet}')

    current_target_os = gclient_config.get('target_os', [])
    builder_target_os = self.m.gclient.c.target_os
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
      error_info = (
          'Caution: your .gclient file and the builder\'s mismatches in'
          ' the following way(s). Please run "gclient sync" after '
          'resolving these:\n' + '\n'.join(mismatch_messages))
    return error_info

  def check_gn_args(self, build_dir: Path,
                    builder_id: chromium.BuilderId) -> str:
    """Check if the args.gn file is acceptable to use for the selected builder

    Args:
        build_dir: Path to the build dir being used
        builder_id: The BuilderId for the builder being run
    Returns:
        A string that represents the error or an empty string when there is no
        warning
    """

    def get_imports(args):
      return [l.strip() for l in args.splitlines() if l.startswith('import(')]

    builder_gn_args = self.m.chromium.mb_lookup(
        builder_id, recursive=False, name='lookup_builder_gn_args')
    builder_imports = get_imports(builder_gn_args)
    builder_gn_args = self.m.gn.parse_gn_args(builder_gn_args)

    current_gn_args, _ = self.m.gn.read_args(build_dir)
    current_imports = get_imports(current_gn_args)
    current_gn_args = self.m.gn.parse_gn_args(current_gn_args)

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

  def check_upstream_branch(self):
    """Check if the current branch has an upstream branch for diffing against

    Returns:
        A string that represents the error or an empty string when there is no
        warning
    """
    if self.get_upstream_branch().retcode != 0:
      return 'Caution: failed to get an upstream branch from the current checkout'

  def get_upstream_branch(self):
    return self.m.git(
        'rev-parse',
        '--abbrev-ref',
        '--symbolic-full-name',
        '@{u}',
        name='check upstream branch',
        stdout=self.m.raw_io.output(),
        raise_on_failure=False,
        step_test_data=lambda: self.m.raw_io.test_api.stream_output(
            'origin/main\n'))

  def prerun_checks(
      self, properties: Request, build_path: Path,
      compiling_builder_id: chromium.BuilderId) -> result_pb2.RawResult:
    # TODO(crbug.com/41492686): Combine these checks so they can be prompted in
    # one interation of the recipe invocations
    def create_prompt_option(prompt: str, **kwargs):
      if not kwargs:
        return (prompt, Request())
      rerun_properties = copy.deepcopy(properties.rerun_options)
      update = Request.RerunOptions(**kwargs)
      rerun_properties.MergeFrom(update)
      return (prompt, rerun_properties)

    if not properties.rerun_options.bypass_gclient:
      error_message = self.check_gclient()
      if error_message:
        rerun_options = [
            create_prompt_option('yes', bypass_gclient=True),
            create_prompt_option('no')
        ]
        return self.create_rerun_result(rerun_options, error_message,
                                        properties.output_properties_file)
    if (self.m.path.exists(build_path / 'args.gn') and
        not properties.rerun_options.bypass_gn_args):
      error_message = self.check_gn_args(build_path, compiling_builder_id)
      if error_message:
        rerun_options = [
            create_prompt_option(
                'continue', bypass_gn_args=True, preserve_gn_args=True),
            create_prompt_option(
                'overwrite', bypass_gn_args=True, preserve_gn_args=False),
            create_prompt_option('abort')
        ]
        return self.create_rerun_result(rerun_options, error_message,
                                        properties.output_properties_file)
    if (not properties.rerun_options.bypass_branch_check and
        self.m.code_coverage.using_coverage and properties.builder_recipe
        in ('chromium/orchestrator', 'chromium_trybot')):
      error_message = self.check_upstream_branch()
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
        return self.create_rerun_result(rerun_options, error_message,
                                        properties.output_properties_file)

  def create_rerun_result(self,
                          rerun_options: list[tuple[str, Request.RerunOptions]],
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
                           message=properties,
                           preserving_proto_field_name=True)))
      self.m.file.write_json(
          f'write output_properties_file {output_properties_file}',
          output_properties_file, output)
    return result_pb2.RawResult(
        status=common_pb2.FAILURE, summary_markdown=info)

  def generate_got_revisions_map(self):
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
    result = self.m.git('rev-parse', 'HEAD', stdout=self.m.raw_io.output())
    rev = result.stdout.decode('utf-8').strip()
    return {
        # See the substitutions in recipe_modules/chromium_tests/generators.py
        # for what got_* revision keys might be used.
        'got_cr_revision': rev,
        'got_revision': rev,
        'got_src_revision': rev,
    }

  def handle_code_coverage(
      self,
      build_dir: Path,
      properties: Request,
      builder_id: chromium.BuilderId,
  ) -> list[str]:
    """Handles code coverage for runs using try builders

    Based on the input properties (bypass_branch_check and skip_instrumentation)
    this will either:
      - Instrument everything by removing the coverage_instrumentation_input_file
      when bypass_branch_check is true and skip_instrumentation is false
      - Instrument nothing by creating an empty instrumentation file when
      bypass_branch_check is true and skip_instrumentation is true
      - Instrument only the changed files when bypass_branch_check is false

    Args:
        build_dir: Path to the directory to use for building
        properties: Request given to the recipe
        builder_id: The ID of the builder to get tests from
    Returns:
        A list of gn args that need to be removed
    """
    # If we're bypassing the branch check and not skipping instrumentation
    # then remove the gn arg to instrument everything
    if (properties.rerun_options.bypass_branch_check and
        not properties.rerun_options.skip_instrumentation):
      return ['coverage_instrumentation_input_file']
    paths = []
    if not properties.rerun_options.skip_instrumentation:
      with self.m.context(cwd=self.m.path.checkout_dir):
        step_result = self.get_upstream_branch()
        branch_upstream_name = step_result.stdout.decode('utf-8').strip()
        step_result = self.m.git(
            '-c',
            'core.quotePath=false',
            'diff',
            '--merge-base',
            '--name-only',
            branch_upstream_name,
            name='git diff to instrument',
            stdout=self.m.raw_io.output(),
            step_test_data=lambda: self.m.raw_io.test_api.stream_output('foo.cc'
                                                                       ))
      paths = [p.decode('utf-8') for p in step_result.stdout.splitlines()]
      paths.sort()
      if self.m.platform.is_win:
        paths = [path.replace('\\', '/') for path in paths]
    self.m.code_coverage.src_dir = self.m.chromium_checkout.source_dir
    self.m.code_coverage.instrument(paths)
    return []

  def get_remote_compile_options(self, build_dir: Path,
                                 properties: Request) -> bool:
    use_reclient = False
    if self.m.chromium.c.project_generator.tool == 'mb':
      gn_args, _ = self.m.gn.read_args(build_dir)
      args = self.m.gn.parse_gn_args(gn_args)
      use_reclient = args.get('use_remoteexec') == 'true' and args.get(
          'use_reclient') != 'false'

    if properties.no_rbe:
      use_reclient = False

    return use_reclient

  def compile_targets(
      self,
      properties: Request,
      tests: Iterable[Test],
      builder_id: chromium.BuilderId,
      preserve_gn_args: bool,
      build_dir: str,
      builder_recipe: str,
  ) -> result_pb2.RawResult:
    """Builds the test targets

    Args:
        properties: Request given to the recipe
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

    gn_args_to_remove = []
    # Only recipes that support try should handle changed files
    if (self.m.code_coverage.using_coverage and
        builder_recipe in ('chromium/orchestrator', 'chromium_trybot')):
      gn_args_to_remove = self.handle_code_coverage(build_dir, properties,
                                                    builder_id)

    gn_args_to_update = {}
    if properties.no_rbe:
      gn_args_to_update['use_remoteexec'] = 'false'
    if properties.no_siso:
      gn_args_to_update['use_siso'] = 'false'

    # If we can use mb_gen it is preferable but if a gn arg needs to be removed
    # or we've been asked to preserve whatever is in the build dir's existing
    # args.gn file then fall back to using gn gen and handling the gn args here
    if gn_args_to_remove or gn_args_to_update:
      preserve_gn_args = True
    if preserve_gn_args:
      if not self.m.path.exists(build_dir / 'args.gn'):
        gn_args = self.m.chromium.mb_lookup(
            builder_id, recursive=False, name='lookup_builder_gn_args')
      else:
        gn_args, _ = self.m.gn.read_args(build_dir)

      if gn_args_to_remove or gn_args_to_update:
        gn_args = gn_args.splitlines()
        arg_re = self.m.gn.ARG_RE
        for arg_line in gn_args:
          match = arg_re.match(arg_line)
          if (match and match.group(1) in gn_args_to_remove or
              match.group(1) in gn_args_to_update):
            gn_args.remove(arg_line)

        for update_arg, update_val in gn_args_to_update.items():
          gn_args.append(f'{update_arg} = {update_val}')

        self.m.file.write_text('write cleaned gn args',
                               build_dir.joinpath('args.gn'),
                               '\n'.join(gn_args))
      self.m.gn.gen(build_dir, 'gn_gen')
    else:
      tests_to_isolate = [t.isolate_target for t in tests if t.isolate_target]
      self.m.chromium.mb_gen(
          builder_id,
          name='generate_build_files',
          recursive_lookup=True,
          build_dir=build_dir,
          isolated_targets=tests_to_isolate)

    use_reclient = self.get_remote_compile_options(build_dir, properties)

    if use_reclient:
      self.m.reclient.experimental_credentials_helper = 'luci-auth'
      self.m.reclient.experimental_credentials_helper_args = ' '.join([
          'token',
          '-scopes-context',
          '-json-output=-',
          '-json-format=reclient',
          '-lifetime=5m',
      ])

    def compile_fn():
      return self.m.chromium.compile(
          targets,
          skip_log_upload=True,
          target_output_dir=str(build_dir),
          use_reclient=use_reclient), preserve_gn_args

    if properties.no_siso:
      with self.m.siso.disable():
        return compile_fn()
    return compile_fn()

  def create_tests(
      self,
      properties: Request,
      build_dir: Path,
      got_revisions: Mapping[str, str],
      builder_id: chromium.BuilderId,
      builder_config: ctbc.BuilderConfig,
      should_build: bool,
  ) -> tuple[result_pb2.RawResult, Iterable[Test]]:
    """Creates the test objects for the provided builder/test names

    Args:
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
    targets_config = self.m.chromium_tests.create_targets_config(
        builder_config, got_revisions, self.m.path.checkout_dir)

    def _get_matching_test(requested_test_name):
      for t in targets_config.all_tests:
        if requested_test_name in (t.name, t.canonical_name):
          return t
      raise self.m.step.StepFailure(
          f'No suites on the bot matched the request for {requested_test_name}')

    tests = [_get_matching_test(n) for n in test_names]

    # TODO(crbug.com/335017001): Disable 'layout tests' archiving since we run
    # ci builders that would point to gcs dirs that devs do not have access to.
    # Remove when layout tests can be archived
    for test in tests:
      if isinstance(test, SwarmingIsolatedScriptTest):
        test.spec = attr.evolve(test.spec, results_handler_name=None)
      if properties.additional_test_args:
        test.spec = attr.evolve(
            test.spec,
            args=test.spec.args + tuple(properties.additional_test_args))

    if properties.reuse_swarming_task:
      self.reuse_swarming_task(properties.reuse_swarming_task, tests)
      return None, tests

    if should_build:
      raw_result, preserve_gn_args = self.compile_targets(
          properties, tests, builder_id, preserve_gn_args, build_dir,
          builder_recipe)
      if raw_result.status != common_pb2.SUCCESS:
        return raw_result, None
    skylab_tests = [test for test in tests if test.is_skylabtest]
    if not should_build or preserve_gn_args or skylab_tests:
      # When compiling, "mb.py gen" will produce the *.isolate files for us. In
      # all other instances, we need to ask mb.py to do so specifically. Do so
      # for *all* possible targets. This shouldn't take much longer, and
      # simplifies things a bit.
      self.m.chromium.mb_isolate_everything(None, build_dir=build_dir)

    isolate_tests = [test for test in tests if test.isolate_target]
    if isolate_tests:
      self.m.chromium_tests.isolate_tests(
          builder_config, isolate_tests, '', '', build_dir=build_dir)

    # TODO(crbug.com/41492686): Prepare skylab artifacts
    return None, tests

  def reuse_swarming_task(
      self,
      reuse_swarming_task: str,
      tests: Iterable[Test],
  ):
    """Applies the cas and execution info from the swarming task to the test

    Args:
      reuse_swarming_task: The swarming task to reuse
      tests: A list of the test to reuse. This should be a single, swarming test
    """
    if len(tests) != 1 or not tests[0].uses_isolate:
      raise self.m.step.StepFailure(
          'Only one test that uses swarming can be reused at a time')

    task = self.m.swarming.show_request('get_reuse_swarming_task',
                                        reuse_swarming_task).to_jsonish()

    test = tests[0]
    task_test_suite = [t for t in task['tags'] if t.startswith('test_suite:')
                      ][0][len('test_suite:'):]
    if test.canonical_name != task_test_suite:
      raise self.m.step.StepFailure(
          'The provided swarming task does not appear to match the requested '
          f'test. Requested {test.canonical_name} but trying to reuse '
          f'{task_test_suite}')
    task_slice = task['task_slices'][0]['properties']

    test.raw_cmd = test.spec.resultdb.unwrap(self.m, task_slice['command'])
    test.relative_cwd = task_slice['relative_cwd']

    digest = task_slice['cas_input_root']['digest']
    swarm_hashes = {
        test.target_name: digest['hash'] + '/' + digest['size_bytes']
    }

    self.m.isolate.set_isolated_tests(swarm_hashes)
