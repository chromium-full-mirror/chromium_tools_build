# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Shareable implementation of the recipe side of Chromium's UTR.

TODO(crbug.com/41492686): Move as much code from
recipes/chromium/universal_test_runner.py into here as possible.
"""

import copy
from google.protobuf import json_format

from recipe_engine import recipe_api
from recipe_engine.config_types import Path

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb2
from PB.recipe_engine import result as result_pb2
from PB.recipe_modules.build.chromium_utr.request import Request
from RECIPE_MODULES.build import chromium


class ChromiumUTRApi(recipe_api.RecipeApi):

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
        build_dir: Path to the directory to use for building
        properties: Request given to the recipe
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
          not self.m.path.exists(build_dir / 'args.gn')):
        gn_args = self.m.chromium.mb_lookup(
            builder_id,
            recursive=False,
            name='lookup_builder_gn_args_for_code_coverage')
      else:
        gn_args, _ = self.m.gn.read_args(build_dir)
      self.m.file.write_text(
          'remove coverage_instrumentation_input_file gn arg',
          build_dir.joinpath('args.gn'), '\n'.join(
              arg for arg in gn_args.split('\n')
              if not arg.startswith('coverage_instrumentation_input_file')))
      return True
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
    self.m.code_coverage.src_dir = self.m.chromium_checkout.src_dir
    self.m.code_coverage.instrument(paths)
    return properties.rerun_options.preserve_gn_args

  def get_remote_compile_options(self, build_dir) -> bool:
    use_reclient = False
    if self.m.chromium.c.project_generator.tool == 'mb':
      gn_args, _ = self.m.gn.read_args(build_dir)
      args = self.m.gn.parse_gn_args(gn_args)
      use_reclient = args.get('use_remoteexec') == 'true'
    return use_reclient
