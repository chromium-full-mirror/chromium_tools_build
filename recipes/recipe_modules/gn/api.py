# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import re

from recipe_engine import recipe_api

from . import constants

class GnApi(recipe_api.RecipeApi):

  def __init__(self, **kwargs):
    super().__init__(**kwargs)
    self._desc_cache = {}

  _DEFAULT_STEP_NAME = 'read GN args'
  _NON_LOCAL_ARGS = frozenset(['target_sysroot'])
  _DEFAULT_MAX_TEXT_LINES = 20

  ARG_RE = re.compile('\s*(\w+)\s*=\s*(\S+)')
  DEFAULT = constants.DEFAULT
  TEXT = constants.TEXT
  LOGS = constants.LOGS

  def read_args(self, build_dir, step_name=None):
    """Read the GN args.

    Args:
      build_dir: The Path to the build output directory. The args.gn file will
        be extracted from this location. The args.gn file must already exist (gn
        or mb should have already been run before calling this method).

    Returns:
      A tuple containing the contents of the args.gn file as a single string and
      the step result of reading the file.
    """
    args_file_path = build_dir / 'args.gn'
    step_name = step_name or self._DEFAULT_STEP_NAME
    fake_args = 'target_cpu = "x86"\n'
    args = self.m.file.read_text(step_name, args_file_path, fake_args)
    return args, self.m.step.active_result

  def reformat_args(self, args):
    """Reformat the GN args to be more useful for local repros.

    Some of the arguments have values that are specific to individual runs and
    so a developer would likely not want to copy them to their own checkout when
    attempting to reproduce an issue locally. To enable ease of use, these
    arguments are moved to the end of the arguments so that a single contiguous
    region can be copied in order to create a build directory with the
    appropriate arguments.

    Args:
      args: A string containing the args.gn content to reformat.

    Returns:
      The reformatted args.gn content as a single string.
    """
    local_lines = []
    non_local_lines = []
    for l in args.splitlines():
      match = self.ARG_RE.match(l)
      if match is not None and match.group(1) in self._NON_LOCAL_ARGS:
        non_local_lines.append(l)
      else:
        local_lines.append(l)
    return '\n'.join(local_lines + non_local_lines)

  def present_args(self, result, args, location=None, max_text_lines=None):
    """Present the GN args.

    Args:
      result: The step result to present the GN args on.
      args: A string containing the args.gn content to present.
      location: Controls where the GN args for the build should be presented. By
        default or if gn.DEFAULT, the args will be in step_text if the count of
        lines is less than max_text_lines or the logs otherwise. To force the
        presentation to the step_text or logs, use gn.TEXT or gn.LOGS,
        respectively.
      max_text_lines: The maximum number of lines of GN args to display in the
        step_text when using the default behavior for displaying GN args.
    """
    location = location or self.DEFAULT
    assert location in (self.DEFAULT, self.TEXT, self.LOGS), \
        "location must be one of gn.DEFAULT, gn.TEXT or gn.LOGS"

    lines = args.splitlines()

    if location == self.DEFAULT:
      if max_text_lines is None:
        max_text_lines = self._DEFAULT_MAX_TEXT_LINES
      if len(lines) > max_text_lines:
        result.presentation.step_text += (
            '<br/>Count of GN args (%d) exceeds limit (%d),'
            ' presented in logs instead') % (len(lines), max_text_lines)
        location = self.LOGS

    if location == self.LOGS:
      result.presentation.logs['gn_args'] = lines
    else:
      result.presentation.step_text += '<br/>'.join([''] + lines)

  def get_args(self, build_dir, location=None, max_text_lines=None,
               step_name=None):
    """Get the GN args for the build.

    A step will be executed that fetches the args.gn file and adds the contents
    to the presentation for the step.

    Args:
      build_dir: The Path to the build output directory. The args.gn file will
        be extracted from this location. The args.gn file must already exist (gn
        or mb should have already been run before calling this method).
      location: Controls where the GN args for the build should be presented. By
        default or if gn.DEFAULT, the args will be in step_text if the count of
        lines is less than max_text_lines or the logs otherwise. To force the
        presentation to the step_text or logs, use gn.TEXT or gn.LOGS,
        respectively.
      max_text_lines: The maximum number of lines of GN args to display in the
        step_text when using the default behavior for displaying GN args.
      step_name: The name of the step for reading the args.

    Returns:
      The content of the args.gn file.
    """
    args, result = self.read_args(build_dir, step_name=step_name)
    reformatted_args = self.reformat_args(args)
    self.present_args(result, reformatted_args,
                      location=location, max_text_lines=max_text_lines)
    return args

  def _gn_cmd(self, name, cmd, gn_path=None, log_name='gn output', **kwargs):
    if not gn_path:
      gn_path = self.m.depot_tools.gn_py_path
    return self.m.step(
        name,
        cmd=['python3', gn_path] + list(cmd),
        stdout=self.m.raw_io.output_text(name=log_name, add_output_log=True),
        **kwargs)

  def refs(self, build_dir, inputs, all_deps=True, output_type=None,
           output_format='label', step_name='calculate gn refs', **kwargs):
    """Find reverse dependencies for a given set of inputs.

    See https://gn.googlesource.com/gn/+/main/docs/reference.md#cmd_refs for
    more documentation of the command.

    Args:
      build_dir: Path to build output directory.
      inputs: List of label or files to find dependencies of.
      all_deps: Boolean indicating wether or not to include indirect
        dependencies.
      output_type: Type of target (eg: "executable", "shared_library", etc.) to
        restrict outputs to. If None (default), no filtering is preformed.
      output_format: How to display targets. See GN docs for valid options.
        Default is "label".
      step_name: Optional recipe step name to give to the "gn refs" command.
      kwargs: Other arguments passed to the underlying python step.
    Returns:
      The set of dependencies found.
    """
    assert isinstance(inputs, list), \
        'Inputs to GN-refs must be a list of files or labels.'
    cmd = [
        'refs',
        '-q',  # Don't print a warning when no refs are found.
        '--as=%s' % output_format,
    ]
    if all_deps:
      cmd += ['--all']
    if output_type:
      cmd += ['--type=%s' % output_type]
    cmd.append(build_dir)
    cmd.extend(inputs)
    step_result = self._gn_cmd(step_name, cmd, log_name='refs', **kwargs)
    output = step_result.stdout
    return set(output.splitlines())

  def desc(self,
           build_dir,
           label_or_pattern,
           what_to_show,
           *flags,
           step_name='Run gn desc',
           use_cache=False,
           **kwargs):
    """Displays information about a given target or config.

    See https://gn.googlesource.com/gn/+/main/docs/reference.md#cmd_desc for
    more documentation of the command.

    Args:
      build_dir: Path to build output directory.
      label_or_pattern: The <label or pattern> can be a target label, a config label, or a label pattern
      what_to_show: type of information we're looking for.
      flags: `what_to_show`-dependent flags to append to the command.
      step_name: Optional recipe step name to give to the "gn desc" command.
      use_cache: Boolean indicating whether or not to use cached results.
        Currently only supports caching for 'deps' with '--all' flag.
      kwargs: Other arguments passed to the underlying python step.
    Returns:
      The list of dependencies found.
    """
    is_cacheable = (what_to_show == 'deps' and '--all' in flags)
    cache_key = (str(build_dir), label_or_pattern, what_to_show, flags)

    if use_cache and is_cacheable and cache_key in self._desc_cache:
      return self._desc_cache[cache_key]

    cmd = ['desc', build_dir, label_or_pattern, what_to_show, *flags]

    step_result = self._gn_cmd(step_name, cmd, log_name='desc', **kwargs)
    output = list(step_result.stdout.splitlines())

    if use_cache and is_cacheable:
      self._desc_cache[cache_key] = output

    return output

  def ls(self, build_dir, inputs, output_type=None, output_format='label',
         step_name='list gn targets'):
    """List targets for a given set of inputs.

    See https://gn.googlesource.com/gn/+/main/docs/reference.md#cmd_ls for
    more documentation of the command.

    Args:
      build_dir: Path to build output directory.
      inputs: List of patterns to find matching targets for.
      output_type: Type of target (eg: "executable", "shared_library", etc.) to
        restrict outputs to. If None (default), no filtering is preformed.
      output_format: How to display targets. See GN docs for valid options.
        Default is "label".
      step_name: Optional recipe step name to give to the "gn ls" command.
    Returns:
      The set of targets found.
    """
    assert isinstance(inputs, list), \
        'Inputs to GN-ls must be a list of file or label patterns.'
    cmd = [
        'ls',
        '--as=%s' % output_format,
    ]
    if output_type:
      cmd += ['--type=%s' % output_type]
    cmd.append(build_dir)
    cmd.extend(inputs)
    step_result = self._gn_cmd(step_name, cmd, log_name='targets')
    output = step_result.stdout
    return set(output.splitlines())

  def gen(self,
          build_dir,
          step_name='gn gen',
          args=None,
          write_ide_json_filename=None):
    """Runs gn gen to generate build files with existing or new gn args

    See https://gn.googlesource.com/gn/+/main/docs/reference.md#cmd_gen for
    more documentation of the command. If `args` is None, the build_dir must
    exist and contain gn args

    Args:
      build_dir: Path to build output directory.
      step_name: Optional recipe step name to give to the "gn gen" command.
      args: Optional args to pass `gn gen`.
      write_ide_json_filename: Optionally generates a JSON file in the
        build_dir containing target information for an IDE.
    """
    with self.m.context(cwd=build_dir):
      cmd = ['gen']
      if args is not None:
        cmd.append('--args=' + args)
      if write_ide_json_filename is not None:
        cmd.append('--ide=json')
        cmd.append('--json-file-name=' + write_ide_json_filename)
      cmd.append(build_dir)
      self._gn_cmd(step_name, cmd)

  def clean(self, build_dir, step_name='clean outdir'):
    """Cleans the output directory except for args.gn and needed ninja files.

    See https://gn.googlesource.com/gn/+/main/docs/reference.md#cmd_clean for
    more documentation of the command. The build_dir must exist and contain
    files from previous gn run.

    Args:
      build_dir: Path to build output directory.
      step_name: Optional recipe step name to give to the "gn clean" command.
    """
    with self.m.context(cwd=build_dir):
      self._gn_cmd(step_name, ['clean', build_dir])

  def parse_gn_args(self, content):
    """Parses string content from arg.gn to a dictionary.

    Args:
      content: The string content of arg.gn file.

    Returns:
      A dictionary of gn args.
    """
    result = {}
    for line in content.splitlines():
      match = self.ARG_RE.match(line)
      if match:
        result[match.group(1)] = match.group(2)
    return result
