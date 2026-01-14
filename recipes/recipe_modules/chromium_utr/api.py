# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Shareable implementation of the recipe side of Chromium's UTR."""

from __future__ import annotations

import typing
import attr
import contextlib
import copy
import itertools
import re
import uuid
from collections.abc import Iterable, Mapping
from google.protobuf import json_format

from recipe_engine import recipe_api
from recipe_engine.config_types import Path

from PB.go.chromium.org.luci.buildbucket.proto import (
    common as common_pb2,
)
from PB.recipe_engine import result as result_pb2
from PB.recipe_modules.build.chromium_utr.request import Request

from RECIPE_MODULES.build import chromium_types
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc
from RECIPE_MODULES.build.chromium.config import get_expected_host_platform

if typing.TYPE_CHECKING:  # pragma: no cover
  from RECIPE_MODULES.build.chromium_tests.steps import Test

gclient_aliases = {
    'linux': ['linux', 'unix'],
    'mac': ['mac', 'osx'],
    'win': ['win', 'windows']
}

GPU_IDENTIFIER_REGEX = re.compile(r'.*on (.*) GPU on .*')


class ChromiumUTRApi(recipe_api.RecipeApi):

  # Special sub-log names added by the UTR recipe to surface to users.
  UTR_LOG_NAME = 'utr_log'

  def __init__(self, env_properties, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self._is_inside_gemini = env_properties.GEMINI_CLI

  def run(
      self,
      properties: Request,
      *args,
      **kwargs,
  ) -> result_pb2.RawResult:
    """Top-level wrapper around the UTR run that will optionally disable siso.

    See _run_impl() below for actual implementation.
    """
    if properties.no_siso:
      cm = self.m.siso.disable()
    else:
      cm = contextlib.nullcontext()
    with cm:
      return self._run_impl(properties, *args, **kwargs)

  def _run_impl(
      self,
      properties: Request,
      checkout_dir: Path,
      source_dir: Path,
      builder_id: chromium_types.BuilderId,
      builder_config: ctbc.BuilderConfig,
  ) -> result_pb2.RawResult:
    """Compiles and runs tests as needed.

    Args:
      properties: Request given to the recipe
      checkout_dir: The directory that the checkout was performed in.
      source_dir: The path to the top-level repo.
      builder_id: BuilderId for the compiler builder
      builder_config: BuilderConfig for the compiling builder

    Returns:
      result_pb2.RawResult of the recipe execution
    """
    should_build = properties.run_type != Request.RunType.RUN_TYPE_RUN
    should_test = properties.run_type != Request.RunType.RUN_TYPE_COMPILE

    compiling_builder_id, compiling_builder_config = self.get_compiling_builder_config(
        builder_id, builder_config)

    build_path = self.configure_build_dir(source_dir, properties.build_dir)
    self.m.profiles.source_dir = source_dir
    self.m.code_coverage.source_dir = source_dir
    self.m.code_coverage.build_dir = build_path

    is_cog = self._is_cog(source_dir)
    result = self.prerun_checks(
        properties,
        source_dir,
        build_path,
        compiling_builder_id,
        compiling_builder_config,
        is_cog,
    )
    if result != None:
      return result

    # TODO(https://crbug.com/374367787): Support getting the revision with cog
    got_revisions = {} if is_cog else self.generate_got_revisions_map(
        source_dir)

    raw_result, tests = self.create_tests(
        properties,
        checkout_dir,
        source_dir,
        build_path,
        got_revisions,
        compiling_builder_id,
        compiling_builder_config,
        should_build,
        should_test,
        is_cog,
    )
    if raw_result and raw_result.status != common_pb2.SUCCESS:
      return raw_result
    # If remote linking is enabled for the given builder, then the resulting
    # compile outputs won't ever be fetched back to the host. The user is
    # likely expecting them, though. So we need to fetch them ourselves. We
    # use Siso to do so since it's more graceful than cas when a file is
    # already present.
    if should_build and self.m.siso.enabled and self.m.siso.without_bytes:
      with self.m.step.nest('download compilation outputs'):
        total_tests_fetched = 0
        for test_name in self.m.isolate.isolated_tests:
          runtime_deps_path = build_path / f'{test_name}.runtime_deps'
          if self.m.path.exists(runtime_deps_path):
            full_deps = self.m.file.read_text(f'read {test_name}.runtime_deps',
                                              runtime_deps_path)
            with self.m.context(cwd=build_path):
              self.m.siso.fs_flush(
                  f'fetch {test_name}',
                  source_dir,
                  full_deps.splitlines(),
              )
              total_tests_fetched += 1
        self.m.step.empty(
            f'fetched outputs for {total_tests_fetched} total tests')
    if not should_test:
      return result_pb2.RawResult(status=common_pb2.SUCCESS)

    # Explicitly disable resultdb for any test we're running locally. We don't
    # want to be uploading results to resultdb locally.
    for t in tests:
      if t.runs_locally:
        t.spec = attr.evolve(
            t.spec, resultdb=attr.evolve(t.spec.resultdb, enable=False))

    if properties.swarming_shards:
      for t in tests:
        if t.runs_on_swarming:
          t.spec = attr.evolve(t.spec, shards=properties.swarming_shards)

    if properties.swarming_dimensions:
      for t in tests:
        if t.runs_on_swarming:
          new_dimensions = dict(t.spec.dimensions)

          for d in properties.swarming_dimensions:
            k, v = d.split('=', 1)
            if not v:
              new_dimensions.pop(str(k), None)
            else:
              new_dimensions[str(k)] = str(v)

          t.spec = attr.evolve(t.spec, dimensions=new_dimensions)

    test_runner = self.m.chromium_tests.create_test_runner(
        checkout_dir,
        source_dir,
        build_path,
        tests,
        include_utr_instruction=True)
    with self.m.chromium_tests.wrap_chromium_tests(
        checkout_dir, source_dir, build_path, tests=tests):
      self.m.chromium_tests.configure_swarming(True)
      # Lower pri for faster turn-around time in debugging. The UTR shouldn't
      # get so much use that it affects CI/CQ traffic substantially. But we can
      # check for sure using the UTR-specific tag below, and reassess if needed.
      self.m.chromium_swarming.default_priority = 20
      self.m.chromium_swarming.add_default_tag('is_utr:1')
      invocation_uuid = str(uuid.uuid4())
      self.m.chromium_swarming.add_default_tag('utr_invocation_uuid:' +
                                               invocation_uuid)

      ret = test_runner()
      if any(not t.has_valid_results('') and t.runs_on_swarming for t in tests):
        step_result = self.m.step.empty('print swarming tasks link')
        step_result.presentation.logs[self.UTR_LOG_NAME] = []
        for s in {
            t.spec.server if t.spec.server else self.m.swarming.current_server
            for t in tests
        }:
          step_result.presentation.logs[self.UTR_LOG_NAME].append(
              f'{s}/tasklist?f=utr_invocation_uuid-tag%3A{invocation_uuid}')

      failed_local_tests = [
          t for t in tests if t.runs_locally and t.failure_on_exit('')
      ]
      if failed_local_tests:
        ret.summary_markdown += '\n\n' + (
            '**NOTE**: some tests failed to upload individual results. These are: '
            '%s. Scroll up to their stdout/stderr to see more info.' %
            (', '.join(t.name for t in failed_local_tests)))

      if any(t for t in tests if not t.runs_locally):
        some_or_all_text = 'all of'
        if failed_local_tests:
          some_or_all_text = 'some of'
        step_result = self.m.step.empty('print results URL')
        step_result.presentation.logs[self.UTR_LOG_NAME] = [
            f'For futher information, {some_or_all_text} the test results have '
            'been uploaded to:',
            self.m.milo.current_results_url,
        ]

      if any(not t.has_valid_results('') and not t.deterministic_failures('')
             for t in tests):
        step_result = self.m.step.empty('unknown failures')
        step_result.presentation.logs[self.UTR_LOG_NAME] = [
            'One or more suites had zero tests fail but exited non-zero.',
            'Try resolving any gclient config warnings and re-running.',
            'Otherwise, can re-run with "-vv" for additional logging.',
        ]

      return ret

  def _is_cog(self, source_dir: Path) -> bool:
    is_cog = str(source_dir).startswith('/google/cog/cloud')
    # TODO(https://crbug.com/374367787): Remove this when cog is fully supported
    if is_cog:
      self.m.step('cog warning', [
          'python3',
          '-c',
          'print("Caution: cog is not fully supported in UTR")',
      ])
    return is_cog

  def get_compiling_builder_config(
      self, builder_id: chromium_types.BuilderId,
      builder_config: ctbc.BuilderConfig
  ) -> tuple[chromium_types.BuilderId, ctbc.BuilderConfig]:
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

    compiling_builder_id = chromium_types.BuilderId.create_for_group(
        builder_config.parent_builder_group, builder_config.parent_buildername)
    compiling_builder_config = attr.evolve(
        builder_config,
        builder_ids=[compiling_builder_id],
        builder_ids_in_scope_for_testing=[builder_id],
    )
    if compiling_builder_config.execution_mode != ctbc.COMPILE_AND_TEST:
      raise self.m.step.StepFailure(
          f'Unsupported UTR invocation for builder {builder_id} triggered by '
          f'builder {builder_config.parent_buildername}. Please file a '
          "general infra bug via https://g.co/bugatrooper if you're seeing "
          'this.')
    return compiling_builder_id, compiling_builder_config

  def configure_build_dir(self, source_dir: Path, build_dir: str):
    if build_dir:
      build_path = self.m.path.cast_to_path(build_dir)
    else:
      build_path = self.m.chromium.default_build_dir(source_dir)

    self.m.file.ensure_directory('ensure_build_dir', build_path)
    self.m.chromium.build_dir = build_path
    return build_path

  def get_gclient_config(self, source_dir: Path):
    src_file = source_dir.parent / '.gclient'
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

  def check_gclient(self, source_dir: Path) -> str:
    """Check if the .gclient file is acceptable to use for the selected builder

    Returns:
        A string that represents the error or an empty string when there is no
        warning
    """
    mismatch_messages = []
    gclient_config = self.get_gclient_config(source_dir)
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

    # If the builder's gclient is using its platform to allow the default
    # target_os we need the gclient file to explicitly set it
    if len(builder_target_os) == 0:
      compiler_platform = (
          self.m.chromium.c.HOST_PLATFORM
          if self.m.chromium.c.HOST_PLATFORM_EXPLICIT else
          get_expected_host_platform(self.m.chromium.c))
      gclient_options = gclient_aliases.get(compiler_platform, None)
      if (gclient_options and
          not any(gclient_plat_option in current_target_os or
                  gclient_plat_option == self.m.platform.name
                  for gclient_plat_option in gclient_options)):
        builder_target_os.add(compiler_platform)

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

  def check_gn_args(
      self,
      source_dir: Path,
      build_dir: Path,
      compiling_builder_id: chromium_types.BuilderId,
      compiling_builder_config: ctbc.BuilderConfig,
      no_siso: bool,
  ) -> str:
    """Check if the args.gn file is acceptable to use for the selected builder

    Args:
        build_dir: Path to the build dir being used
        builder_id: The BuilderId for the builder being run
        compiling_builder_id: BuilderId for the compiling builder
        compiling_builder_config: BuilderId for the given builder
        no_siso: Disables siso if True.
    Returns:
        A string that represents the error or an empty string when there is no
        warning
    """

    def get_imports(args):
      return [l.strip() for l in args.splitlines() if l.startswith('import(')]

    builder_gn_args = self.m.chromium.mb_lookup(
        source_dir,
        compiling_builder_id,
        recursive=False,
        phase=compiling_builder_config.mb_phase_for_tests,
        name='lookup_builder_gn_args')
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
        if arg == 'use_siso' and current_gn_args[arg] == 'false' and no_siso:
          # Don't need to print a warning if siso is expected to not match.
          continue
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
      args_filename = str(build_dir / 'args.gn')
      error_info = (f'Caution: your build\'s gn args set in {args_filename} '
                    'and the builder\'s mismatches in the following way(s):\n' +
                    '\n'.join(mismatch_messages))
    return error_info

  def check_upstream_branch(self, source_dir: Path):
    """Check if the current branch has an upstream branch for diffing against

    Returns:
        A string that represents the error or an empty string when there is no
        warning
    """
    if self.get_upstream_branch(source_dir).retcode != 0:
      return 'Caution: failed to get an upstream branch from the current checkout'

  def get_upstream_branch(self, source_dir: Path):
    with self.m.context(cwd=source_dir):
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

  def create_prompt_option(self, properties: Request, prompt: str, **kwargs):
    if not kwargs:
      return (prompt, Request())
    rerun_properties = copy.deepcopy(properties.rerun_options)
    update = Request.RerunOptions(**kwargs)
    rerun_properties.MergeFrom(update)
    return (prompt, rerun_properties)

  def prerun_checks(
      self,
      properties: Request,
      source_dir: Path,
      build_path: Path,
      compiling_builder_id: chromium_types.BuilderId,
      compiling_builder_config: ctbc.BuilderConfig,
      is_cog: bool,
  ) -> result_pb2.RawResult:
    """Checks that local conditions are right to continue with the build.

    Args:
      properties: Request given to the recipe
      source_dir: The path to the top-level repo
      build_path: Path to the build dir being used
      compiling_builder_id: BuilderId for the compiling builder
      compiling_builder_config: BuilderId for the compiling builder
      is_cog: The run is currently in cog

    Returns:
      None if everything is OK. Otherwise a RawResult that will trigger a rerun
    """
    # TODO(crbug.com/41492686): Combine these checks so they can be prompted in
    # one interation of the recipe invocations

    if not properties.rerun_options.bypass_gclient:
      error_message = self.check_gclient(source_dir)
      if error_message:
        rerun_options = [
            self.create_prompt_option(
                properties, 'ignore and proceed', bypass_gclient=True),
            self.create_prompt_option(properties, 'abort')
        ]
        return self.create_rerun_result(rerun_options, error_message,
                                        properties.output_properties_file)
    if (self.m.path.exists(build_path / 'args.gn') and
        not properties.rerun_options.bypass_gn_args):
      error_message = self.check_gn_args(source_dir, build_path,
                                         compiling_builder_id,
                                         compiling_builder_config,
                                         properties.no_siso)
      if error_message:
        rerun_options = [
            self.create_prompt_option(
                properties,
                'continue',
                bypass_gn_args=True,
                preserve_gn_args=True),
            self.create_prompt_option(
                properties,
                'overwrite',
                bypass_gn_args=True,
                preserve_gn_args=False),
            self.create_prompt_option(properties, 'abort')
        ]
        return self.create_rerun_result(rerun_options, error_message,
                                        properties.output_properties_file)
    if (not properties.rerun_options.bypass_branch_check and
        self.m.code_coverage.using_coverage and properties.builder_recipe
        in ('chromium/orchestrator', 'chromium_trybot') and not is_cog):
      error_message = self.check_upstream_branch(source_dir)
      if error_message:
        rerun_options = [
            self.create_prompt_option(
                properties,
                'instrument everything',
                bypass_branch_check=True,
                skip_instrumentation=False),
            self.create_prompt_option(
                properties,
                'skip instrumentation',
                bypass_branch_check=True,
                skip_instrumentation=True),
            self.create_prompt_option(properties, 'abort')
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

  def generate_got_revisions_map(self, source_dir: Path):
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
    with self.m.context(cwd=source_dir):
      result = self.m.git('rev-parse', 'HEAD', stdout=self.m.raw_io.output())
    rev = result.stdout.decode('utf-8').strip()
    rev_map = {
        # See the substitutions in recipe_modules/chromium_tests/generators.py
        # for what got_* revision keys might be used.
        'got_cr_revision': rev,
        'got_revision': rev,
        'got_src_revision': rev,
    }
    # TODO(crbug.com/395081730): Remove this magic prop plumbing.
    self.m.chromium.set_build_properties(rev_map)
    return rev_map

  def handle_code_coverage(
      self,
      source_dir: Path,
      build_dir: Path,
      properties: Request,
      builder_id: chromium_types.BuilderId,
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
      with self.m.context(cwd=source_dir):
        step_result = self.get_upstream_branch(source_dir)
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
    self.m.code_coverage.instrument(paths)
    return []

  def gn_gen(
      self,
      properties: Request,
      builder_id: chromium_types.BuilderId,
      builder_config: ctbc.BuilderConfig,
      preserve_gn_args: bool,
      source_dir: Path,
      build_dir: Path,
      builder_recipe: str,
      is_cog: bool,
      isolate_targets: Iterable[str] = None,
  ) -> result_pb2.RawResult:
    """Generates a build dir that matches the given builder.

    Args:
        properties: Request given to the recipe
        builder_id: The ID of the builder to compile for
        builder_config: BuilderConfig of the given builder
        preserve_gn_args: Bool whether to have the recipe overwrite the gn args
          with the builder_id's gn args
        build_dir: Path to the directory to use for building
        builder_recipe: The recipe normally run by the requested builder
        is_cog: The run is currently in cog
        isolate_targets: Name of test targets to isolate
    Returns a bool indicating if isolates for the tests were generated.
    """
    isolate_targets = isolate_targets or []
    gn_args_to_remove = []
    # Only recipes that support try should handle changed files
    if (self.m.code_coverage.using_coverage and
        builder_recipe in ('chromium/orchestrator', 'chromium_trybot') and
        not is_cog):
      gn_args_to_remove = self.handle_code_coverage(source_dir, build_dir,
                                                    properties, builder_id)

    gn_args_to_update = {}
    if properties.no_rbe:
      gn_args_to_update['use_remoteexec'] = 'false'
    if properties.no_siso:
      gn_args_to_update['use_siso'] = 'false'

    # If we can use mb_gen it is preferable but if a gn arg needs to be removed
    # or we've been asked to preserve whatever is in the build dir's existing
    # args.gn file then fall back to using gn gen and handling the gn args here
    missing_isolates = (
        preserve_gn_args or gn_args_to_remove or gn_args_to_update)
    if missing_isolates:
      if not self.m.path.exists(build_dir / 'args.gn'):
        gn_args = self.m.chromium.mb_lookup(
            source_dir,
            builder_id,
            recursive=False,
            phase=builder_config.mb_phase_for_tests,
            name='lookup_builder_gn_args')
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
      self.m.chromium.mb_gen(
          source_dir,
          build_dir,
          builder_id,
          name='generate_build_files',
          recursive_lookup=True,
          phase=builder_config.mb_phase_for_tests,
          isolated_targets=isolate_targets)

    return not missing_isolates

  def gn_gen_and_compile_targets(
      self,
      properties: Request,
      tests: Iterable[Test],
      builder_id: chromium_types.BuilderId,
      builder_config: ctbc.BuilderConfig,
      preserve_gn_args: bool,
      source_dir: Path,
      build_dir: Path,
      builder_recipe: str,
      is_cog: bool,
      compile_only_targets: Iterable[str],
  ) -> result_pb2.RawResult:
    """Builds the test targets

    Args:
        properties: Request given to the recipe
        tests: Iterable of test objects to be compiled
        builder_id: The ID of the builder to compile for
        builder_config: BuilderConfig of the given builder
        preserve_gn_args: Bool whether to have the recipe overwrite the gn args
          with the builder_id's gn args
        build_dir: Path to the directory to use for building
        builder_recipe: The recipe normally run by the requested builder
        is_cog: The run is currently in cog
        compile_only_targets: additional compile targets to compile
    Returns tuple of (a RawResult object for the compile or None if it was
        skipped, a boolean indicating if the *.isolate files were generated)
    """
    targets = list(itertools.chain(
        *[t.compile_targets() for t in tests])) + list(compile_only_targets)

    # Remove duplicate targets.
    targets = sorted(set(targets))

    isolate_targets = [t.isolate_target for t in tests if t.isolate_target]
    with self.m.context(env=self.m.chromium.get_env(source_dir)):
      generated_isolates = self.gn_gen(properties, builder_id, builder_config,
                                       preserve_gn_args, source_dir, build_dir,
                                       builder_recipe, is_cog, isolate_targets)

    # Some tests don't require anything to be compiled.
    if not targets:
      return None, generated_isolates

    def _run_compile():
      extra_ninja_args = []
      if self._is_inside_gemini:
        extra_ninja_args.append('--quiet')
      return self.m.chromium.compile(
          source_dir,
          build_dir,
          targets=targets,
          skip_log_upload=True,
          extra_ninja_args=extra_ninja_args,
          ninja_path=properties.autoninja_path), generated_isolates

    # We disable siso via recipes here in order to force chromium.compile() to
    # invoke our specified autoninja directly. However, siso will likely get
    # re-enabled within autoninja itself.
    if properties.autoninja_path:
      with self.m.siso.disable():
        return _run_compile()
    else:
      return _run_compile()


  def create_tests(
      self,
      properties: Request,
      checkout_dir: Path,
      source_dir: Path,
      build_dir: Path,
      got_revisions: Mapping[str, str],
      builder_id: chromium_types.BuilderId,
      builder_config: ctbc.BuilderConfig,
      should_build: bool,
      should_test: bool,
      is_cog: bool,
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
        should_test: Bool controlling whether the tests will be run
        is_cog: The run is currently in cog
    """
    test_names = properties.test_names
    preserve_gn_args = properties.rerun_options.preserve_gn_args
    builder_recipe = properties.builder_recipe
    targets_config = self.m.chromium_tests.create_targets_config(
        builder_config,
        got_revisions,
        source_dir,
        build_dir,
        checkout_dir=checkout_dir,
        force_experimental_tests=True,
    )

    def _get_matching_test(requested_test_name):
      for t in targets_config.all_tests:
        if requested_test_name in (t.name, t.canonical_name):
          return t

      # Try again with GPU info modified for some backwards compatibility with
      # runs before crrev.com/c/7103332. This can be removed once enough time
      # has passed that users are unlikely to try to use the UTR with a build
      # from before that CL.
      match = GPU_IDENTIFIER_REGEX.match(requested_test_name)
      if match:
        original_gpu_identifier = match.group(1)
        # There are potentially multiple identifiers if the GPU dimension
        # contains |, but prior to the inclusion of the model, only the first
        # one was surfaced. So, only look at the first identifier if multiple
        # are present.
        gpu_identifier = original_gpu_identifier.split('/')[0]
        replacement_identifier = gpu_identifier
        # Known GPU vendor with a GPU model.
        split_identifier = gpu_identifier.split()
        if len(split_identifier) > 1:
          replacement_identifier = split_identifier[0]
        elif ('(' in gpu_identifier and ':' in gpu_identifier and
              ')' in gpu_identifier):
          # Unknown GPU vendor with a GPU model.
          gpu_vendor = gpu_identifier.split('(')[1].split(':')[0]
          replacement_identifier = '(%s)' % gpu_vendor
        prefix, _, suffix = requested_test_name.partition(
            original_gpu_identifier)
        requested_test_name = prefix + replacement_identifier + suffix
        for t in targets_config.all_tests:
          if requested_test_name in (t.name, t.canonical_name):
            return t

      raise self.m.step.StepFailure(
          'No suites on the bot matched the request for '
          f'{requested_test_name}. Passed-in tests must either all be test '
          'suite names, or all be paths to test files.')

    # An empty list of tests implies we should use all
    tests = targets_config.all_tests
    additional_compile_targets = targets_config.compile_only_targets
    if test_names:
      # If all passed-in tests are paths, assume they're test files. In which
      # case, hand this off to autotest.py so it can do its thing. We need to
      # test both abspath and relpaths here for the test since expectation tests
      # only accept abspaths, but the user may have passed a relpath.
      if all(
          self.m.path.exists(source_dir / t) or self.m.path.exists(t)
          for t in test_names):
        # autotest.py will take care of everything (ninja invocation, test
        # invocation) except preparing a build dir. So we still need to run
        # GN gen prior to handing off to autotest.py.
        self.gn_gen(properties, builder_id, builder_config, preserve_gn_args,
                    source_dir, build_dir, builder_recipe, is_cog)
        return self.run_autotest(source_dir, build_dir, list(test_names)), []
      tests = [_get_matching_test(n) for n in test_names]
      additional_compile_targets = [
          t for t in targets_config.compile_only_targets if t in test_names
      ]

    def _matches_multiple_tests(filter_arg):
      """Returns True if the given filter arg matches just a single test case.

      Currently, gtests's and isolated-script-tests's filter arg API use the
      same format to specify multiple tests in one filter. Namely "*" and ":"
      characters. See:
      https://google.github.io/googletest/advanced.html#running-a-subset-of-the-tests
      https://chromium.googlesource.com/chromium/src/+/main/docs/testing/test_executable_api.md#Filtering-which-tests-to-run

      So we simply use those the presence of those characters to determine if
      the filter matches one or more tests. Note that there's no guarantee
      that a new test type won't be introduced that uses some other format for
      filtering. So this is only best-effort, and might be very fragile.
      """
      return ('*' in filter_arg or ':' in filter_arg)

    # avoid circular import
    # pylint: disable=import-outside-toplevel
    from RECIPE_MODULES.build.chromium_tests.steps import (
        SwarmingIsolatedScriptTest)

    for test in tests:
      # TODO(crbug.com/335017001): Disable 'layout tests' archiving since we run
      # ci builders that would point to gcs dirs that devs do not have access
      # to. Remove when layout tests can be archived

      if isinstance(test, SwarmingIsolatedScriptTest):
        test.spec = attr.evolve(test.spec, results_handler_name=None)
      # Prevent deduping in case the user is trying to run the same suite in
      # parallel multiple times to suss out flakiness.
      if test.runs_on_swarming:
        test.spec = attr.evolve(test.spec, idempotent=False)
      if test.runs_on_skylab and (properties.additional_test_args or
                                  properties.omit_default_test_args):
        raise self.m.step.StepFailure(
            'Modifying test arguments is not supported on skylab tests')
      if properties.omit_default_test_args:
        test.spec = attr.evolve(test.spec, args=tuple())
      if properties.additional_test_args:
        test.spec = attr.evolve(
            test.spec,
            args=test.spec.args + tuple(properties.additional_test_args))
        # Running a single test case of a suite with multiple shards will lead
        # to all but one shard being a no-op. This has lead to some confusion
        # among users. So we try to detect such a case and set shard count to
        # one.
        filters = []
        for arg in properties.additional_test_args:
          if arg.startswith(test.option_flags.filter_flag):
            filters.append(arg)
        if len(filters) == 1 and not _matches_multiple_tests(filters[0]):
          if test.runs_on_swarming:
            test.spec = attr.evolve(test.spec, shards=1)

    if properties.reuse_swarming_task:
      self.reuse_swarming_task(properties.reuse_swarming_task, tests)
      return None, tests

    if should_build:
      raw_result, generated_isolates = self.gn_gen_and_compile_targets(
          properties,
          tests,
          builder_id,
          builder_config,
          preserve_gn_args,
          source_dir,
          build_dir,
          builder_recipe,
          is_cog,
          additional_compile_targets,
      )
      if raw_result and raw_result.status != common_pb2.SUCCESS:
        return raw_result, None
    skylab_tests = [test for test in tests if test.runs_on_skylab]
    if not should_build or not generated_isolates or skylab_tests:
      # When compiling, "mb.py gen" will produce the *.isolate files for us. In
      # all other instances, we need to ask mb.py to do so specifically. Do so
      # for *all* possible targets. This shouldn't take much longer, and
      # simplifies things a bit.
      self.m.chromium.mb_isolate_everything(
          source_dir, build_dir, None, phase=builder_config.mb_phase_for_tests)

    isolate_tests = [test for test in tests if test.isolate_target]
    if isolate_tests:
      self.m.chromium_tests.isolate_tests(
          source_dir,
          build_dir,
          builder_config,
          isolate_tests,
          '',
          '',
      )

    if skylab_tests and should_test:
      self.m.chromium_tests.prepare_artifact_for_skylab(builder_config,
                                                        checkout_dir,
                                                        source_dir, build_dir,
                                                        skylab_tests)

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

  def run_autotest(self, source_dir, build_dir, test_files):
    """Invokes autotest.py for the given test files.

    Args:
        source_dir: The path to the top-level repo.
        build_dir: Path to the directory to use for building
        test_files: Test files to pass to autotest.py
    Returns a RawResult for autotest.py invocation.
    """
    cmd = [
        'python3',
        source_dir.joinpath('tools', 'autotest.py'),
        '-C',
        build_dir,
    ] + test_files
    with self.m.context(cwd=source_dir):
      result = self.m.step(
          'invoke autotest.py',
          cmd,
          raise_on_failure=False,
      )
    return result_pb2.RawResult(
        status=common_pb2.FAILURE if result.retcode else common_pb2.SUCCESS,
        summary_markdown='autotest.py failure')
