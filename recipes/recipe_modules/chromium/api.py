# Copyright 2013 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import collections
import collections.abc
import contextlib
from collections.abc import Iterable
import functools
import hashlib
import os
import re
import textwrap

from typing import Any

from recipe_engine import recipe_api
from recipe_engine.config_types import Path

import RECIPE_MODULES.build.chromium_types as chromium
from .config import validate_config

from PB.recipe_engine import result as result_pb2
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb

from RECIPE_MODULES.build.chromium_utr.instruction import get_utr_compile_instruction

_CR_COMPILE_GUARD_NAME = 'CR_COMPILE_GUARD.txt'
_CR_COMPILE_GUARD_CONTENTS = textwrap.dedent("""\
    This file exists while a build compiles and is removed at the end of
    compilation. If the next build finds that the file exists prior to
    compilation, it will wipe the output directory.

    See https://crbug.com/959436 for more context.
    """)

# During Ninja to Siso migration,
# Builders want to detect the build system switch and clean up the build
# directory.
# The file content is either "ninja" or "siso".
# TODO: b/315393741 - Remove this logic after Siso migration.
_LAST_BUILD_SYSTEM = 'LAST_BUILD_SYSTEM.txt'

class ChromiumApi(recipe_api.RecipeApi):
  # Tag used to indicate a step can be used for bisect.
  BISECT_STEP_TAG = 'luci-bisection.is_bisectable'

  def __init__(self, input_properties, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self._build_dir = None
    self._build_properties = None
    self._version = None
    self._clang_version = None
    self._rust_version = None
    self._verify_config = True
    # TODO(yueshe@) - migrate this property to xcode module once downstream
    # no longer sets this property
    self._xcode_build_version = input_properties.xcode_build_version
    self._fail_build_on_clang_warnings = input_properties.fail_build_on_clang_warnings


  @property
  def xcode_build_version(self):
    return self._xcode_build_version

  @property
  def fail_build_on_clang_warnings(self):
    return self._fail_build_on_clang_warnings

  @property
  def verify_config(self):
    return self._verify_config

  @verify_config.setter
  def verify_config(self, value: bool) -> None:
    self._verify_config = value

  def make_config_params(self, *args, **kwargs):
    config_object, params = super().make_config_params(*args, **kwargs)
    if config_object is not None and self._verify_config:
      validate_config(config_object, self.m.platform.name)
    return config_object, params

  def apply_config(self, config_name, config_object=None, optional=False):
    super().apply_config(config_name, config_object, optional)
    if self._verify_config:
      validate_config(config_object or self.c, self.m.platform.name)

  @contextlib.contextmanager
  def chromium_layout(self):
    """Context manager that must be entered prior to performing any Chromium
    recipe operations. This is responsible for basic enviornment initialization.
    """

    with self.m.context(
        env={
            # CHROME_HEADLESS makes sure that running 'gclient runhooks' and
            # other tools don't require user interaction.
            'CHROME_HEADLESS': '1',
        }):
      yield

  def _with_chromium_layout(fn):
    """Decorator which applies "ensure_chromium_layout" to bound ChromiumApi
    functions.

    This is an INTERNAL method, and specifically decorates ChromiumApi member
    functions. DO NOT USE this outside of this class and module.
    """

    @functools.wraps(fn)
    def inner(self, *args, **kwargs):
      with self.chromium_layout():
        return fn(self, *args, **kwargs)

    return inner

  def get_config_defaults(self):
    defaults = {
        'HOST_PLATFORM': None,
        'DEFAULT_HOST_PLATFORM': self.m.platform.name,
        'HOST_ARCH': self.m.platform.arch,
        'HOST_BITS': self.m.platform.bits,
        'TARGET_PLATFORM': self.m.platform.name,
        'TARGET_ARCH': self.m.platform.arch,
        'TARGET_CROS_BOARDS': None,
        'CROS_BOARDS_WITH_QEMU_IMAGES': None,

        # NOTE: This is replicating logic which lives in
        # chrome/trunk/src/build/common.gypi, which is undesirable. The desired
        # end-state is that all the configuration logic lives in one place (in
        # chromium/config.py), and the buildside gypfiles are as dumb as
        # possible. However, since the recipes need to accurately contain
        # {TARGET,HOST}_{BITS,ARCH,PLATFORM}, for use across many tools (of
        # which gyp is one tool), we're taking a small risk and replicating the
        # logic here.
        'TARGET_BITS':
            (32 if self.m.platform.name == 'win' else self.m.platform.bits),
        'BUILD_CONFIG': self.m.properties.get('build_config', 'Release'),
        'TEST_ONLY': False,
    }

    return defaults

  def get_env(self, source_dir: Path) -> dict[str, Any]:
    """Get the environemnt to use for chromium steps.

    Args:
      source_dir: The path to the top-level repo.
    """
    ret = {}
    if self.c.env.PATH:
      ret['PATH'] = self.m.path.pathsep.join(
          [str(source_dir / p) for p in self.c.env.PATH] + ['%(PATH)s'])
    if self.c.env.FORCE_MAC_TOOLCHAIN:
      ret['FORCE_MAC_TOOLCHAIN'] = self.c.env.FORCE_MAC_TOOLCHAIN
    return ret

  @property
  def build_properties(self):
    return self._build_properties

  def targets_spec_dir(self, source_dir: Path) -> Path:
    """The path to the targets spec directory."""
    return source_dir / self.c.targets_spec_dir

  def analyze_config_path(self, source_dir: Path) -> Path:
    """The path to the analyze config file."""
    return source_dir / self.c.analyze_config_path

  def default_build_dir(self, source_dir: Path) -> Path:
    """The path to the directory containing built outputs.

    Args:
      source_dir: The path to the top-level repo.
    """
    if self.c.shared_build_dir:
      return source_dir / 'out' / f'shared-{self.c.build_config_fs}'
    # Add "/" to prevent collisions since it can't show up in a builder name.
    hash_input = '{}/{}/{}'.format(
        self.m.buildbucket.build.builder.project,
        self.m.buildbucket.build.builder.bucket,
        self.m.buildbucket.build.builder.builder).encode()
    hash_part = hashlib.sha256(hash_input).hexdigest()[:4]
    builder_name_part = ('_'.join(
        self.m.buildbucket.build.builder.builder.split())[:15])
    return source_dir / 'out' / (hash_part + '-' + builder_name_part)

  def _default_ninja_path(self, source_dir: Path) -> Path:
    """The path to the ninja executable.

    Args:
      source_dir: The path to the top-level repo.
    """
    return source_dir / 'third_party/ninja/ninja'

  def get_version(self, source_dir: Path) -> dict[str, str]:
    """Returns a dictionary describing the version.

    The dictionary will map the name of the portion of the version to its
    numeric value e.g.
    { 'MAJOR'": '51', 'MINOR': '0', 'BUILD': '2704', 'PATCH': '0' }

    Args:
      source_dir: The path to the top-level repo.
    """
    if self._version is None:
      self._version = self.get_version_from_file(source_dir / 'chrome/VERSION')
    return self._version

  def get_version_from_file(self, version_file_path, step_name='get version'):
    """Returns the version information from a specified file.

    The dictionary will map the name of the portion of the version to its
    numeric value e.g.
    { 'MAJOR'": '51', 'MINOR': '0', 'BUILD': '2704', 'PATCH': '0' }
    """
    text = self.m.file.read_text(
        step_name,
        version_file_path,
        test_data="MAJOR=51\nMINOR=0\nBUILD=2704\nPATCH=0\n")
    version = {}
    for line in text.splitlines():
      [k, v] = line.split('=', 1)
      version[k] = v
    return version

  def set_build_properties(self, props):
    self._build_properties = props

  def get_builder_id(self):
    builder_group = self.m.builder_group.for_current
    buildername = self.m.buildbucket.builder_name
    return chromium.BuilderId.create_for_group(builder_group, buildername)

  def configure_bot(self, builders_dict, additional_configs=None):
    """Sets up the configurations and gclient to be ready for bot update.

    builders_dict is a dict of builder_group -> buildername -> bot_config.

    The current builder_group and buildername are looked up from the
    build properties; we then apply the configs specified in bot_config
    as appropriate.

    Returns:
      A tuple of (builder_id, bot_config) for subsequent use in the
      recipe.
    """
    additional_configs = additional_configs or []

    # TODO: crbug.com/358481 . The build_config should probably be a property,
    # but that doesn't exist today, so we
    # need a lookup mechanism to map bot name to build_config.
    builder_id = self.get_builder_id()
    group_dict = builders_dict.get(builder_id.group, {})
    bot_config = group_dict.get('builders', {}).get(builder_id.builder)

    self.set_config(bot_config.chromium_config or 'chromium',
                    **bot_config.chromium_config_kwargs)

    for c in bot_config.chromium_apply_config:
      self.apply_config(c)

    for c in additional_configs:
      self.apply_config(c)

    # Note that we have to call gclient.set_config() and apply_config() *after*
    # calling chromium.set_config(), above, because otherwise the chromium
    # call would reset the gclient config to its defaults.
    self.m.gclient.set_config('chromium')
    for c in bot_config.gclient_apply_config:
      self.m.gclient.apply_config(c)

    return (builder_id, bot_config)

  def _limit_error_list(self,
                        error_list,
                        char_limit,
                        message_prefix='',
                        message_suffix='',
                        line_format='{}',
                        limit_hint=''):
    """Limits combined length of strings and formats the error list.

    Args:
      error_list: list of strings meant to be formatted
      char_limit: max amount of characters within the list of strings
      message_prefix: string added to the beginning of the list
      message_suffix: string added to the end of the list
      line_format: (Uses string.format()) Used to format each item in list
      limit_hint: string added after last item if limit is reached

    Returns:
      A formatted list of errors
    """
    char_count = 0
    errors = []
    for error in error_list:
      error_line = line_format.format(error)
      char_count += len(error_line)

      if char_count > char_limit:
        return [message_prefix] + errors + [message_suffix, limit_hint]

      errors.append(error_line)

    return [message_prefix] + errors + [message_suffix]

  def _format_failures(self,
                       failure_summary,
                       step_name,
                       footer='',
                       char_limit=1000,
                       line_limit=1000):
    """Removes non-vital information from summary and adds markdown.

    Args:
      failure_summary: string of error information from a compile step,
      step_name: string in header that shows what step failed,
      error_regex: regex used to identify errors in summary,
      footer: message appended at the end of the summary,
      char_limit: max size failure summary can be,
      line_limit: max size of each line in the summary

    Returns:
      A string containing a markdown formatted failure summary of
      the step that failed.
    """
    if self._test_data.enabled:
      char_limit = self._test_data.get('change_char_size_limit', 350)
      line_limit = self._test_data.get('change_line_limit', 100)

    # The estimated length of a line will be 1 / 7th of the char limit.
    # The default case will 100 characters per line
    AVG_LINE_SIZE = (char_limit // 7)

    summary_lines = failure_summary.splitlines()
    for index in range(len(summary_lines)):
      if len(summary_lines[index]) > line_limit:
        summary_lines[index] = (
            summary_lines[index][:AVG_LINE_SIZE] + '...(too long)')

    CODE_TAG = '```'

    summary_lines = self._limit_error_list(
        summary_lines,
        char_limit,
        message_prefix=CODE_TAG,
        message_suffix=CODE_TAG,
        limit_hint='##### ...The message was too long...')

    # Header and footer are not reduced previously because
    # they have markdown and should not be encased in code tags.

    # Add header and footer
    header = '#### Step _%s_ failed. Error logs are shown below:' % step_name
    summary_lines.insert(0, header)
    # Ensure footer is within a reasonable size,
    if len(footer) <= 3 * AVG_LINE_SIZE:
      summary_lines.append('#### %s' % footer)

    return '\n'.join(summary_lines)

  # TODO: crbug.com/339375951 - Move this logic to infra recipe modules.
  def _get_logdog_url(self, step, log):
    sanitized_name_tokens = [
        re.sub('[ _/()]', '_', t) for t in step.name_tokens
    ]
    url = 'https://%(logdog_hostname)s/logs/%(logdog_project)s/%(logdog_prefix)s/+/u/%(step_name)s/%(log_name)s' % {
        'logdog_hostname': self.m.buildbucket.build.infra.logdog.hostname,
        'logdog_project': self.m.buildbucket.build.infra.logdog.project,
        'logdog_prefix': self.m.buildbucket.build.infra.logdog.prefix,
        'step_name': '/'.join(sanitized_name_tokens),
        'log_name': log,
    }
    return url

  def _run_ninja(self,
                 source_dir: Path,
                 ninja_command,
                 *,
                 name=None,
                 ninja_env=None,
                 ninja_extra_args=None,
                 siso_args=None,
                 skip_log_upload=False,
                 resource_usage_output_file=None,
                 ninja_invocation_id=None,
                 include_utr_instruction=False,
                 builder_id=None,
                 **kwargs):
    """
    Run ninja with given command and env.

    Args:
      source_dir: The path to the top-level repo.
      ninja_command: Command used for build.
                     This is sent as part of log.
                     (e.g. ['ninja', '-C', 'out/Release'])
      name: Name of compile step.
      ninja_env: Environment for ninja.
      siso_args: Siso specific arguments.
      skip_log_upload: When true skip log uploading.
      resource_usage_output_file: File which if provided will record the resource usage
                                 stats related to build step
      ninja_invocation_id: ID of the build invocation of the compile step.
      include_utr_instruction: Whether or not to include UTR reproduction
                               instructions
      builder_id: ID for the builder compiling the targets.


    Returns:
      A named tuple with the fields
        - failure_summary: string of the error that occurred during the step,
        - retcode: return code of the step

    Raises:
      InfraFailure from compile step
      StepFailure from compile confirm no-op step
    """

    CompileResult = collections.namedtuple(
        'CompileResult',
        'failure_summary failure_summary_url retcode',
        defaults=(None, None, None))

    failure_output = self.m.raw_io.output_text(
        add_output_log='on_failure', name='failure_summary')

    example_json = {
        'failures': [{
            'output_nodes': ['a.o'],
            'rule': 'CXX',
            'output': '''\
        filename:row:col: error: error info''',
            'dependencies': ['b/a.cc']
        }]
    }
    example_failure_output = textwrap.dedent("""\
        [1/1] CXX a.o
        filename:row:col: error: error info
    """)
    step_test_data = (lambda: self.m.json.test_api.output(
        example_json, name='ninja_info') + self.m.raw_io.test_api.output_text(
            example_failure_output, name='failure_summary'))

    ninja_step_result = None
    try:
      if self.m.siso.enabled:
        # TODO(b/288534744): support ninja_info with Siso.
        cmd = ['ninja', '-failure_summary', failure_output] + ninja_command[1:]
        if ninja_extra_args:
          cmd.extend(ninja_extra_args)
        ninja_step_result = self.m.siso.run_ninja(
            source_dir,
            cmd,
            ninja_env=ninja_env,
            siso_args=siso_args,
            name=name,
            step_test_data=step_test_data,
            skip_log_upload=skip_log_upload,
            resource_usage_output_file=resource_usage_output_file,
            ninja_invocation_id=ninja_invocation_id,
            include_utr_instruction=include_utr_instruction,
            builder_id=builder_id,
            **kwargs)
      else:
        cmd = [
            'vpython3',
            self.resource('ninja_wrapper.py'),
            '--ninja_info_output',
            self.m.json.output(add_json_log='on_failure', name='ninja_info'),
            '--failure_output',
            failure_output,
        ]
        if resource_usage_output_file:
          cmd.append('--resource_usage_output_file')
          cmd.append(resource_usage_output_file)
        cmd.append('--')
        cmd.extend(ninja_command)
        if ninja_extra_args:
          cmd.extend(ninja_extra_args)
        with self.m.context(env=ninja_env):
          ninja_step_result = self.m.step(
              name or 'compile', cmd, step_test_data=step_test_data, **kwargs)
          if ninja_invocation_id:
            ninja_step_result.presentation.tags[
                'ninja_invocation_id'] = ninja_invocation_id

          if include_utr_instruction and builder_id:
            get_utr_compile_instruction(self, ninja_step_result, builder_id)
    except self.m.step.StepFailure as ex:
      ninja_step_result = ex.result
      if ninja_invocation_id and 'ninja_invocation_id' not in ninja_step_result.presentation.tags:
        ninja_step_result.presentation.tags[
            'ninja_invocation_id'] = ninja_invocation_id
      # Ensure the repro instructions are attached for failing compiles
      if (include_utr_instruction and builder_id and
          'resultdb.instruction.id' not in ninja_step_result.presentation.tags):
        get_utr_compile_instruction(self, ninja_step_result, builder_id)
      if ninja_step_result.retcode != 1:
        raise self.m.step.InfraFailure(
            ninja_step_result.name, result=ninja_step_result)

      failure_summary = ('(retcode=%d) No failure summary provided.' %
                         ninja_step_result.retcode)
      failure_summary_url = ''
      if ninja_step_result.raw_io.output_text:
        failure_summary = ninja_step_result.raw_io.output_text
        failure_summary_url = self._get_logdog_url(
            ninja_step_result, 'raw_io.output_text_failure_summary_')

      return CompileResult(
          failure_summary=failure_summary,
          failure_summary_url=failure_summary_url,
          retcode=ninja_step_result.retcode)

    finally:
      if ninja_step_result:
        ninja_step_result.presentation.tags[self.BISECT_STEP_TAG] = 'true'
      if include_utr_instruction and builder_id:
        self.m.repro_instructions.update_invocation_instructions()
      if not self.m.runtime.in_global_shutdown and not skip_log_upload:
        clang_crashreports_script = (
            source_dir / 'tools/clang/scripts/process_crashreports.py')
        if self.m.path.exists(clang_crashreports_script):
          source = '%s-%s' % (self.m.builder_group.for_current,
                              self.m.buildbucket.builder_name)
          if self.m.buildbucket.build.number:
            source += '-%s' % self.m.buildbucket.build.number

          crash_step = self.m.step(
              'process clang crashes',
              ['python3', clang_crashreports_script, '--source', source],
              stdout=self.m.raw_io.output_text(),
              step_test_data=lambda: self.m.raw_io.test_api.stream_output_text(
                  '    gs://chrome-clang-crash-reports/v1/2023/01/01/user-base.tgz\n'
              ),
              **kwargs)

          if crash_step and crash_step.stdout:
            for line in crash_step.stdout.splitlines():
              line = line.strip()
              if line.startswith('gs://'):
                url = line.replace('gs://', 'https://storage.cloud.google.com/')
                filename = line.split('/')[-1]
                crash_step.presentation.links[filename] = url

        self.m.ninjalog.upload(name, ninja_command, ninja_step_result.retcode,
                               ninja_invocation_id)

    if self.fail_build_on_clang_warnings:
      result = self.check_for_clang_warnings(self.m.siso._ninja_dir(cmd))
      if result:
        return CompileResult(
            failure_summary=result, failure_summary_url='', retcode=1)


    ninja_command_explain = ninja_command + ['-d', 'explain', '-n']

    ninja_no_work = 'ninja: no work to do.'

    # Once we've compiled once, a second attempt to compile should do nothing.
    # Any actual work we do here indicates that the dependency graph in GN is
    # misconfigured somehow, which is bad and should break the build.
    noop_step_name = (name or 'compile') + ' confirm no-op'
    noop_step_test_data = lambda: self.m.raw_io.test_api.stream_output_text(
        ninja_no_work)

    def check_noop(step_result):
      if ninja_no_work in step_result.stdout:
        # No dependency issue found.
        return
      step_result.presentation.step_text = (
          "This should have been a no-op, but it wasn't.")
      step_result.presentation.status = self.m.step.FAILURE

    if self.m.siso.enabled:
      step_result = self.m.siso.run_ninja(
          source_dir,
          ninja_command_explain,
          siso_args=siso_args,
          name=noop_step_name,
          step_test_data=noop_step_test_data,
          stdout=self.m.raw_io.output_text(),
          post_step_func=check_noop,
          skip_log_upload=skip_log_upload,
          ninja_invocation_id=ninja_invocation_id,
          **kwargs)
    else:
      with self.m.context(env=ninja_env):
        step_result = self.m.step(
            noop_step_name,
            ninja_command_explain,
            stdout=self.m.raw_io.output_text(),
            step_test_data=noop_step_test_data)
      check_noop(step_result)

    failure_summary_url = ''
    if step_result.presentation.status == self.m.step.FAILURE:
      failure_summary_url = self._get_logdog_url(step_result, 'stderr')
      return CompileResult(
          failure_summary=textwrap.dedent("""
              Failing build because ninja reported work to do.
              This means that after completing a compile, another was run and
              it resulted in still having work to do (that is, a no-op build
              wasn't a no-op). Consult the first "ninja explain:" line for a
              likely culprit.
           """).strip(),
          failure_summary_url=failure_summary_url,
          retcode=1)
    return CompileResult(
        failure_summary='No dependency issues found',
        failure_summary_url=failure_summary_url,
        retcode=ninja_step_result.exc_result.retcode)

  def check_for_clang_warnings(self, build_dir):
    """
    Scan the compiler output (contained in build_dir / 'siso_output') for any
    warnings. If any were present, fail the build.

    Note that chromium usually builds with -Werror, so clang will only emit
    warnings if that flag is turned off.

    It would be preferable to use the WARNING status instead of FAILURE, but
    that doesn't seem to be supported: crbug.com/40581344

    Args:
      build_dir: The path to the build directory used by ninja, which contains
                 the siso_output file.

    Returns:
      None

    Raises:
      StepFailure from the 'scan siso_output for warnings' step
    """

    with self.m.step.nest('check for compile warnings'):
      step_text = 'No warnings found'
      log_text = ''
      status = self.m.step.SUCCESS
      failure_summary = ''

      siso_output_path = os.path.join(build_dir, 'siso_output')
      if self.m.path.exists(siso_output_path):
        siso_output = self.m.file.read_text('read siso_output',
                                            siso_output_path)
      else:
        siso_output = ''
        step_text = str(siso_output_path) + ' does not exist'

      # Regex matching a warning outputted by clang.
      m = list(re.finditer(r'warning:.+\[-W.+\]', siso_output))
      if m:
        first_warning = m[0]
        warning_count = len(m)

        plural = 'warnings' if warning_count != 1 else 'warning'
        step_text = (
            f'Clang emitted {warning_count} {plural} during compilation. '
            'See siso_output for full details.')
        log_text = ('warning text (see siso_output for full details):\n' +
                    first_warning.group(0))
        status = self.m.step.FAILURE

        # Extract the compiler output for the first warning for convenient
        # display to the user. The relevant text blob in siso_output will start
        # with 'build step:' and end with 'X warning(s) generated.'
        end_match = re.search(r'warnings? generated.', siso_output)

        # Find the _last_ instance of 'build step' preceding the warning text
        start_matches = list(
            re.finditer(r'build step:', siso_output[:first_warning.start()]))
        start_match = start_matches[-1] if start_matches else None

        if start_match and end_match:
          output_blob = siso_output[start_match.start():end_match.end()]
          failure_summary = step_text + '\n\n' + output_blob

      # Strip ansi color codes from siso_output text, since they're hard to read
      ansi_color_regex = re.compile(r'(\x9B|\x1B\[)[0-?]*[ -\/]*[@-~]')
      log_text = ansi_color_regex.sub('', log_text)
      failure_summary = ansi_color_regex.sub('', failure_summary)

      if len(log_text) > 1024:
        log_text = log_text[:1024] + '...'

      # Don't raise immediately, so that we can manually raise with
      # a nicer error message.
      self.m.step.empty(
          'scan siso_output for warnings',
          status,
          step_text,
          log_text,
          raise_on_failure=False)

      if m:
        return failure_summary

  @contextlib.contextmanager
  def guard_compile(self, build_dir: Path, *, suffix=''):
    """Ensure that the output directory gets cleaned for the following
    conditions.

    1) Compile was interrupted during the last build.
    On entry, this context manager checks for the existence of a sentinel file
    inside the output directory, cleaning the output directory if it's present.
    It then creates the sentinel file.

    On orderly, non-exception exit, this context manager removes the sentinel
    file.

    The result of using this context manager will be that, if something
    untoward happens while in scope -- build cancellation, unexpected infra
    failure, etc -- the output directory will be clobbered during the next
    build.

    2) Build system switches from the last build.
    Before each compile, this context manager records the build system used in
    this build. ("ninja" or "siso")
    If the build system is different from the recorded one during the next
    build, it cleans the output directory.
    TODO: b/315393741 - Remove this logic after Siso rollout.

    Args:
      build_dir: The path to the directory containing built outputs.
      suffix: A suffix to add to step names.
    """
    should_clean = False
    guard_path = build_dir / _CR_COMPILE_GUARD_NAME
    if self.m.path.exists(guard_path):
      should_clean = True
      clean_reason = 'the last compile step was interrupted'

    build_system = 'siso' if self.m.siso.enabled else 'ninja'
    last_build_system_path = build_dir / _LAST_BUILD_SYSTEM
    if self.m.path.exists(last_build_system_path):
      last_build_system = self.m.file.read_text('read %s' % _LAST_BUILD_SYSTEM,
                                                last_build_system_path)
      if last_build_system and last_build_system != build_system:
        should_clean = True
        clean_reason = ('build system switches from %s to %s' %
                        (last_build_system, build_system))

    if should_clean:
      self.m.file.rmtree('remove unreliable output dir' + suffix, build_dir)
      clean_step_presentation = self.m.step.active_result.presentation
      clean_step_presentation.step_text = 'reason: ' + clean_reason

    self.m.file.ensure_directory('ensure output directory' + suffix, build_dir)
    self.m.file.write_text('create compile guard' + suffix, guard_path,
                           _CR_COMPILE_GUARD_CONTENTS)
    self.m.file.write_text('write %s' % _LAST_BUILD_SYSTEM,
                           last_build_system_path, build_system)

    try:
      yield
    finally:
      if not self.m.runtime.in_global_shutdown:
        self.m.file.remove('remove compile guard' + suffix, guard_path)

  def cleandead(self, source_dir: Path, build_dir: Path):
    """Removes the no longer needed output files from the build directory.

    Args:
      source_dir: The path to the top-level repo.
      build_dir: The path to the directory containing built outputs.
    """
    ninja_env = self.get_env(source_dir)
    ninja_env.update(self.m.context.env)

    command = [str(self._default_ninja_path(source_dir))]
    if self.m.siso.enabled:
      command = [str(self.m.siso.siso_path(source_dir)), 'ninja']
    command += ['-C', build_dir, '-t', 'cleandead']
    with self.m.context(env=ninja_env, cwd=source_dir):
      self.m.step(name='cleandead', cmd=command)

  @_with_chromium_layout
  def compile(self,
              source_dir: Path,
              build_dir: Path,
              *,
              targets=None,
              name=None,
              include_utr_instruction: bool = False,
              builder_id: chromium.BuilderId | None = None,
              ninja_path: str = None,
              extra_ninja_args: list[str] | None = None,
              **kwargs):
    """Return a compile.py invocation.

    Args:
      source_dir: The path to the top-level repo.
      build_dir: The path to the directory containing built outputs.
      targets: List of build targets to compile. If empty, compile targets will
        be taken from chromium.c.compile_py.default_targets. If 'all' is one of
        the targets, no targets will appear on the ninja command line, which
        invokes ninja's behavior to build all targets that do not appear as an
        input to another target.
      name: Name of compile step.
      resource_usage_output_file (BasePath): Path to the file which will hold stats related
                                            to resource usage while compiling
      include_utr_instruction: Whether or not to include instructions using utr
      builder_id: BuilderId for the compile being run
      ninja_path: Custom path to ninja binary.
      extra_ninja_args: Additional Ninja args.

    Returns:
      A RawResult object with the compile step's status and failure message
    """
    targets = targets or self.c.compile_py.default_targets.as_jsonish()
    assert isinstance(targets, (list, tuple)), type(targets)

    if self.c.use_gyp_env:
      # Get the Clang and/or Rust revisions before compiling
      if self.c.gyp_env.GYP_DEFINES.get('clang', 0) == 1:
        self._clang_version = self.get_clang_version(source_dir)

      if self.c.gyp_env.GYP_DEFINES.get('rust', 0) == 1:
        self._rust_version = self.get_rust_version(source_dir)

    ninja_env = self.get_env(source_dir)
    ninja_env.update(self.m.context.env)

    if self.c.compile_py.mode and self.c.compile_py.mode == 'official':
      ninja_env['CHROMIUM_BUILD'] = '_google_chrome'
      # Official builds are always Google Chrome.
      ninja_env['CHROME_BUILD_TYPE'] = '_official'
      # This may be needed for running `ninja -t msvc` in
      # src/build/toolchain/win/BUILD.gn.
      # Note that this may not be needed when ninja is launched directly since
      # Windows does search for the directory of the parent process (which is
      # also ninja). However, when ninja is launched under another subprocess
      # (such as cmd), this is necessary. Therefore, adding the ninja's
      # directory directly will make the code less brittle.
      # TODO(crbug.com/872740): Remove once msvc -t processes are no longer
      # needed.
      if self.c.TARGET_PLATFORM == 'win':
        ninja_env['PATH'] = self.m.path.pathsep.join(
            ('%(PATH)s', str(self._default_ninja_path(source_dir).parent)))

    if ninja_path:
      command = [ninja_path, '-C', str(build_dir)]
    else:
      command = [
          str(self._default_ninja_path(source_dir)), '-C',
          str(build_dir)
      ]

    if self.c.compile_py.build_args:
      command.extend(self.c.compile_py.build_args)

    if targets is not None and 'all' not in targets:
      command += targets

    assert 'env' not in kwargs
    assert 'cwd' not in kwargs

    with self.m.context(cwd=self.m.context.cwd or source_dir):
      ninja_invocation_id = self.m.uuid.random()
      ninja_result = self._run_ninja(
          source_dir,
          name=name or 'compile',
          ninja_command=command,
          ninja_extra_args=extra_ninja_args,
          ninja_env=ninja_env,
          ninja_invocation_id=ninja_invocation_id,
          include_utr_instruction=include_utr_instruction,
          builder_id=builder_id,
          **kwargs)

    if ninja_result.retcode:
      footer = ''
      if ninja_result.failure_summary_url:
        footer = '[Click here for more information](%s)' % ninja_result.failure_summary_url
      failure_summary = self._format_failures(ninja_result.failure_summary,
                                              name or 'compile', footer)
      return result_pb2.RawResult(
          status=common_pb.FAILURE, summary_markdown=failure_summary)

    return result_pb2.RawResult(status=common_pb.SUCCESS)

  @_with_chromium_layout
  def runtest(self,
              checkout_dir: Path,
              build_dir: Path,
              test,
              *,
              args=None,
              xvfb=False,
              name=None,
              builder_group=None,
              parse_gtest_output=False,
              test_type=None,
              python_mode=False,
              test_launcher_summary_output=None,
              **kwargs):
    """Return a runtest.py invocation.

    Args:
      checkout_dir: The path to the directory where the checkout was performed.
      build_dir: The path to the build dir.
      test: Name of the test suite to run.
    """
    args = args or []
    assert isinstance(args, collections.abc.Sequence), repr(args)

    t_name, ext = self.m.path.splitext(self.m.path.basename(test))
    if not python_mode and self.m.platform.is_win and ext == '':
      test += '.exe'

    full_args = ['--build-dir', build_dir]
    if self.c.TARGET_PLATFORM == 'android':
      full_args.extend(['--test-platform', 'android'])
    if self.m.platform.is_linux:
      full_args.append('--xvfb' if xvfb else '--no-xvfb')

    if parse_gtest_output:
      full_args.append('--parse-gtest-output')

    if test_type:
      full_args.append('--test-type=%s' % test_type)
    step_name = name or t_name
    if test_launcher_summary_output:
      full_args.extend(
          ['--test-launcher-summary-output', test_launcher_summary_output])

    # These properties are specified on every bot, so pass them down
    # unconditionally.
    full_args.append('--builder-name=%s' % self.m.buildbucket.builder_name)
    if builder_group is not None:
      full_args.append('--builder-group=%s' % builder_group)
    # A couple of the recipes contain tests which don't specify a buildnumber,
    # so make this optional.
    if self.m.buildbucket.build.number is not None:
      full_args.append('--build-number=%s' % self.m.buildbucket.build.number)
    if ext == '.py' or python_mode:
      full_args.append('--run-python-script')

    if (self.c.runtests.enable_asan or self.c.runtests.run_asan_test):
      full_args.append('--enable-asan')
    if self.c.runtests.enable_lsan:
      full_args.append('--enable-lsan')
    if self.c.runtests.enable_msan:
      full_args.append('--enable-msan')
    if self.c.runtests.enable_tsan:
      full_args.append('--enable-tsan')
    full_args.append(test)

    full_args.extend(args)

    runtest_path = self.repo_resource('recipes', 'runtest.py')
    with self.m.context(cwd=checkout_dir):
      resultdb = kwargs.pop('resultdb', None)
      cmd = ['python3', runtest_path] + full_args
      if resultdb:
        cmd = resultdb.wrap(self.m, cmd, step_name=name)
      return self.m.step(
          step_name,
          cmd,
          **kwargs,
      )

  @_with_chromium_layout
  def get_clang_version(self, source_dir: Path, **kwargs):
    """Get the version of clang.

    Args:
      source_dir: The path to the top-level repo.
    """
    with self.m.context(env=self.get_env(source_dir)):
      update_script = source_dir / 'tools' / 'clang' / 'scripts' / 'update.py'
      cmd = ['python3', update_script, '--print-revision']
      if self.c.use_tot_clang:
        cmd.append('--llvm-force-head-revision')
      step_result = self.m.step(
          name='clang_revision',
          cmd=cmd,
          stdout=self.m.raw_io.output_text(),
          step_test_data=lambda: self.m.raw_io.test_api.stream_output_text(
              'llvmorg-whatever-g123456-7'),
          **kwargs)
      clang_revision = step_result.stdout.strip()
      step_result.presentation.properties['clang_revision'] = clang_revision
      step_result.presentation.step_text = clang_revision
    return clang_revision

  @_with_chromium_layout
  def get_rust_version(self, source_dir: Path, **kwargs):
    """Get the installed version of rust.

    Args:
      source_dir: The path to the top-level repo.
    """
    with self.m.context(env=self.get_env(source_dir)):
      update_script = source_dir / 'tools' / 'rust' / 'update_rust.py'
      cmd = ['python3', update_script, '--print-revision', 'installed']
      step_result = self.m.step(
          name='rust_revision',
          cmd=cmd,
          stdout=self.m.raw_io.output_text(),
          step_test_data=lambda: self.m.raw_io.test_api.stream_output_text(
              '1234-5-llvmorg-whatever-g123456'),
          **kwargs)
      rust_revision = step_result.stdout.strip()
      step_result.presentation.properties['rust_revision'] = rust_revision
      step_result.presentation.step_text = rust_revision
    return rust_revision

  def get_mac_toolchain_installer(self):
    assert self.c.mac_toolchain.installer_cipd_package
    assert self.c.mac_toolchain.installer_version
    assert self.c.mac_toolchain.installer_cmd

    cipd_root = self.m.path.start_dir
    cipd_pkg = self.c.mac_toolchain.installer_cipd_package
    pkg_version = self.c.mac_toolchain.installer_version
    cmd = self.c.mac_toolchain.installer_cmd
    self.m.cipd.ensure(
        cipd_root,
        self.m.cipd.EnsureFile().add_package(cipd_pkg, pkg_version))
    return cipd_root / cmd

  def ensure_mac_toolchain(self, checkout_dir: Path):
    if not self.c.mac_toolchain.enabled or self.c.HOST_PLATFORM != 'mac':
      return

    # Currently the xcode version is being read from the repo
    # for non-branched builders.
    # For branched builders, xcode version is read from xcode_build_version,
    # when xcode configs file path is not specified.
    xcode_build_version = (
        self.m.xcode.get_xcode_version(checkout_dir) or
        self.xcode_build_version or
        self.m.properties.get('xcode_build_version', None))

    if not xcode_build_version:
      raise self.m.step.StepFailure(
          'No Xcode version was provided as a recipe property.')

    kind = self.c.mac_toolchain.kind or self.c.TARGET_PLATFORM
    # TODO(sergeyberezin): for LUCI migration, this must be a requested named
    # cache. Make sure it exists, to avoid downloading Xcode on every build.
    xcode_app_path = self.m.path.cache_dir.joinpath('xcode_%s_%s.app' %
                                                    (kind, xcode_build_version))

    with self.m.step.nest('ensure xcode') as step_result:
      step_result.step_text = ('Ensuring Xcode version %s in %s' %
                               (xcode_build_version, xcode_app_path))

      mac_toolchain_cmd = self.get_mac_toolchain_installer()
      install_args = [
          mac_toolchain_cmd,
          'install',
          '-kind',
          kind,
          '-xcode-version',
          xcode_build_version,
          '-output-dir',
          xcode_app_path,
      ]

      self.m.step('install xcode', install_args, infra_step=True)
      self.m.step(
          'select xcode', ['sudo', 'xcode-select', '-switch', xcode_app_path],
          infra_step=True)

      # Kill all ibtoold processes. When multiple Xcode version is used on the
      # same bot, multiple ibtoold processes from different Xcode might cause
      # compile failues. See crbug.com/1297159. The cmd returns 0 if processes
      # found, 1 if not found.
      self.m.step(
          'kill ibtoold', ['pkill', '-f', '/ibtoold($| )'],
          ok_ret=(0, 1),
          infra_step=True)

      # (crbug.com/1115022) - When the last running simulator is from XCode
      # version n-1, XCode version n throws a failure message. Running simctl
      # w/ to do something as simple as listing devices helps work around this.
      self.m.step('reload simctl', ['xcrun', 'simctl', 'list'], infra_step=True)

  def ensure_toolchains(self, checkout_dir: Path):
    """Ensure necessary toolchains are present.

    Args:
      checkout_dir: The path to the directory where the checkout was performed.
    """
    if self.c.HOST_PLATFORM == 'mac':
      self.ensure_mac_toolchain(checkout_dir)

  @_with_chromium_layout
  def runhooks(
      self,
      source_dir: Path,
      build_dir: Path,
      *,
      env=None,
      clobber=None,
      **kwargs,
  ):
    """Run the build-configuration hooks for chromium.

    Args:
      source_dir: The path to the top-level repo.
      build_dir: The path to the directory containing built outputs.
      env: Additional env vars to set during runhooks
      clobber: Whether to clobber the out dir prior to running hooks. Defaults
        to the "clobber_before_runhooks" chromium config when not specified.
      kwargs: Passthrough args to gclient.runhooks()
    """
    # runhooks might write things into the output directory, so clobber before
    # that. 'clobber_before_runhooks' is true for bots that apply the 'clobber'
    # config.
    if clobber is None:
      clobber = self.c.clobber_before_runhooks
    if clobber:
      self.m.file.rmtree('clobber', build_dir)

    runhooks_env = self.get_env(source_dir)
    runhooks_env.update(self.m.context.env)
    runhooks_env.update(env or {})

    # On Mac, when mac toolchain installation is not enabled in the recipe, the
    # toolchain is installed in runhooks, which requires the installer binary.
    if self.c.HOST_PLATFORM == 'mac':
      runhooks_env['MAC_TOOLCHAIN_INSTALLER'] = (
          self.get_mac_toolchain_installer())

    if self.c.use_gyp_env:
      # TODO(sbc): Ideally we would not need gyp_env set during runhooks when
      # we are not running gyp, but there are some hooks (such as sysroot
      # installation that peek at GYP_DEFINES and modify thier behaviour
      # accordingly.
      runhooks_env.update(self.c.gyp_env.as_jsonish())

    # If there was a bot_update step, reuse it's instruction for the hooks
    tag = self.m.repro_instructions.get_step_instruction_tag(r'.*bot_update')
    with self.m.gsutil.configure_gsutil():
      with self.m.context(
          cwd=self.m.context.cwd or source_dir, env=runhooks_env):
        step_result = None
        try:
          step_result = self.m.gclient.runhooks(**kwargs)
        finally:
          if step_result and tag:
            step_result.presentation.tags['resultdb.instruction.id'] = tag
            self.m.repro_instructions.update_invocation_instructions()

  @_with_chromium_layout
  def run_gn(
      self,
      source_dir: Path,
      build_dir: Path,
      *,
      gn_path=None,
      use_remoteexec=False,
      **kwargs,
  ):
    """Run gn gen to generate the ninja files.

    Args:
      source_dir: The path to the top-level repo.
      build_dir: The path to the directory containing built outputs.
      use_remoteexec: If True, `use_remoteexec=true` will be added to GN args.
    """
    if not gn_path:
      gn_path = self.m.depot_tools.gn_py_path

    gn_args = list(self.c.gn_args)

    if self.c.BUILD_CONFIG == 'Debug':
      gn_args.append('is_debug=true')
    if self.c.BUILD_CONFIG == 'Release':
      gn_args.append('is_debug=false')

    if self.c.TARGET_PLATFORM == 'android':
      gn_args.append('target_os="android"')
    elif self.c.TARGET_PLATFORM in ('mac', 'win'):
      assert self.c.TARGET_ARCH in ('arm', 'intel')
    elif self.c.TARGET_PLATFORM == 'linux':
      assert self.c.TARGET_ARCH in ('arm', 'intel', 'mips')

    gn_cpu = {
        ('intel', 32): 'x86',
        ('intel', 64): 'x64',
        ('arm', 32): 'arm',
        ('arm', 64): 'arm64',
        ('mips', 32): 'mips',
        ('mipsel', 32): 'mipsel',
        ('riscv64', 64): 'riscv64',
    }.get((self.c.TARGET_ARCH, self.c.TARGET_BITS))
    if gn_cpu:
      gn_args.append('target_cpu="%s"' % gn_cpu)

    gn_env = self.get_env(source_dir)

    if use_remoteexec:
      gn_args.append('use_remoteexec=true')

    gn_args.extend(self.c.project_generator.args)

    cmd = [
        gn_path,
        '--root=%s' % str(source_dir),
        'gen',
        build_dir,
        '--args=%s' % ' '.join(gn_args),
    ]
    if str(gn_path).endswith('.py'):
      cmd = ['python3'] + cmd
    with self.m.context(cwd=source_dir, env=gn_env):
      self.m.step(name='gn', cmd=cmd, **kwargs)

  def _mb_isolate_map_file_args(self, source_dir):
    for isolate_map_path in self.c.project_generator.isolate_map_paths:
      yield '--isolate-map-file'
      yield source_dir / isolate_map_path

    if (len(self.c.project_generator.isolate_map_paths) > 1 and
        self.c.project_generator.allow_dup_isolate_entry):
      yield '--allow-dup-isolate-entry'

  @_with_chromium_layout
  def run_mb_cmd(self,
                 name,
                 mb_command,
                 source_dir: Path,
                 builder_id,
                 *,
                 mb_path=None,
                 mb_config_path=None,
                 chromium_config=None,
                 phase=None,
                 android_version_code=None,
                 android_version_name=None,
                 additional_args=None,
                 include_instruction=False,
                 additional_instructions=None,
                 **kwargs):
    """Run an arbitrary mb command.

    Args:
      name: The name of the step.
      mb_command: The mb command to run.
      source_dir: The path to the top-level repo.
      builder_id: The ID of the builder of the configuration to run mb for.
      mb_path: The path to the source directory containing the mb.py script. If
        not provided, the subdirectory tools/mb within the source tree will be
        used.
      mb_config_path: The path to the configuration file containing the builder
        specifications to be used by mb. If not provided, the
        project_generator.config_path config value will be used. If that is
        falsey, then mb_config.pyl under the directory identified by mb_path
        will be used.
      chromium_config: The chromium config object to use. If not provided,
        self.c will be used.
      additional_args: Any args to the mb script besides those for setting the
        group, builder and the path to the config file.
      include_instruction: When true includes instructions to reproduce the step
      additional_instructions: Additional string to add to the end of the
        reproduction instruction content
      **kwargs: Additional arguments to be forwarded onto the python API.
    """
    chromium_config = chromium_config or self.c

    mb_path = mb_path or source_dir / 'tools/mb'
    if not mb_config_path and chromium_config.project_generator.config_path:
      mb_config_path = (
          source_dir / chromium_config.project_generator.config_path)
    mb_config_path = mb_config_path or mb_path / 'mb_config.pyl'

    args = [
        mb_command,
    ]

    # If builder_id is not set the gn args already in the path are used
    if builder_id:
      args.extend([
          '-m',
          builder_id.group,
          '-b',
          builder_id.builder,
      ])
    args.extend([
        '--config-file',
        mb_config_path,
    ])

    if phase is not None:
      args += ['--phase', str(phase)]

    if android_version_code:
      args += ['--android-version-code=%s' % android_version_code]
    if android_version_name:
      args += ['--android-version-name=%s' % android_version_name]
    # TODO(crbug.com/1060857): Remove this once swarming task templates
    # support command prefixes.
    if self.c.project_generator.use_luci_auth:
      args += ['--luci-auth']

    combined_args = args + (additional_args or [])

    cmd = ['python3', '-u', mb_path / 'mb.py'] + combined_args

    # If an environment was provided, copy it so that we don't modify the
    # caller's data
    # This runs with an almost-bare env being passed along, so we get a clean
    # environment without any GYP_DEFINES being present to cause confusion.
    env = self.get_env(source_dir)

    env.update(self.m.context.env)

    with self.m.context(cwd=source_dir, env=env):
      step_result = self.m.step(name, cmd, **kwargs)

      instruction_cmd = 'From your Chromium checkout:<br/>'
      if include_instruction:
        # Remove swarming targets, luci-auth, and output json. These are all for
        # automation/running remotely
        if '--luci-auth' in cmd:
          cmd.remove('--luci-auth')

        def remove_arg_pair(flag):
          if flag in cmd:
            index = cmd.index(flag)
            del cmd[index:index + 2]

        remove_arg_pair('--json-output')
        remove_arg_pair('--swarming-targets-file')

        def sanitize_arg(arg):
          if isinstance(arg, Path):
            arg = str(self.m.path.relpath(arg, source_dir))
          if len(arg.split()) > 1:
            return f'"{arg}"'
          return arg

        cmd = ' '.join([sanitize_arg(arg) for arg in cmd])

        instruction_cmd += f'```{cmd}```'

        if additional_instructions:
          instruction_cmd += '<br/>' + additional_instructions

        self.m.repro_instructions.add_step_instruction(
            step_result,
            local_content=instruction_cmd,
        )
      return step_result


  @_with_chromium_layout
  def mb_analyze(self,
                 source_dir: Path,
                 build_dir: Path,
                 builder_id,
                 analyze_input,
                 *,
                 name=None,
                 mb_path=None,
                 mb_config_path=None,
                 chromium_config=None,
                 phase=None,
                 test_analyze_output=None,
                 **kwargs):
    """Determine which targets need to be built and tested.

    Args:
      source_dir: The path to the top-level repo.
      build_dir: The path to the directory containing built outputs.
      builder_id: The ID of the builder with the build configuration to
        analyze.
      analyze_input: a dict of the following form:
        {
          'files': ['affected/file1', 'affected/file2', ...],
          'test_targets': ['test_target1', 'test_target2', ...],
          'additional_compile_targets': ['target1', 'target2', ...],
        }

    Returns:
      The StepResult from the analyze command.
    """
    name = name or 'analyze'
    mb_args = ['-v']
    mb_args.extend(self._mb_isolate_map_file_args(source_dir))
    mb_args.append(build_dir)
    mb_args.extend([self.m.json.input(analyze_input), self.m.json.output()])
    mb_args.extend(
        ['--json-output',
         self.m.json.output(name="failure_summary")])

    test_analyze_output = test_analyze_output or {
        'status': 'No dependency',
        'compile_targets': [],
        'test_targets': [],
    }
    step_test_data = (lambda: self.m.json.test_api.output(test_analyze_output) +
                      self.m.json.test_api.output({}, name='failure_summary'))
    with self.mb_failure_handler(name):
      return self.run_mb_cmd(
          name,
          'analyze',
          source_dir,
          builder_id,
          mb_path=mb_path,
          mb_config_path=mb_config_path,
          chromium_config=chromium_config,
          phase=phase,
          additional_args=mb_args,
          step_test_data=step_test_data,
          **kwargs)

  @_with_chromium_layout
  def mb_lookup(self,
                source_dir: Path,
                builder_id,
                *,
                name=None,
                mb_path=None,
                mb_config_path=None,
                recursive=False,
                chromium_config=None,
                phase=None,
                android_version_code=None,
                android_version_name=None,
                gn_args_location=None,
                gn_args_max_text_lines=None):
    """Lookup the GN args for the build.

    Args:
      source_dir: The path to the top-level repo.
      builder_id: The ID of the builder for the build configuration to look up.
      name: The name of the step. If not provided 'lookup GN args' will be used.
      mb_path: The path to the source directory containing the mb.py script. If
        not provided, the subdirectory tools/mb within the source tree will be
        used.
      mb_config_path: The path to the configuration file containing the builder
        specifications to be used by mb. If not provided, the
        project_generator.config_path config value will be used. If that is
        falsey, then mb_config.pyl under the directory identified by mb_path
        will be used.
      recursive: Whether the lookup should recursively expand imported args
        files.
      chromium_config: The chromium config object to use. If not provided,
        self.c will be used.
      gn_args_location: Controls where the GN args for the build should be
        presented. By default or if gn.DEFAULT, the args will be in step_text if
        the count of lines is less than gn_args_max_text_lines or the logs
        otherwise. To force the presentation to the step_text or logs, use
        gn.TEXT or gn.LOGS, respectively.
      gn_args_max_text_lines: The maximum number of lines of GN args to display
        in the step_text when using the default behavior for displaying GN args.

    Returns:
      The content of the args.gn file.

    Raises:
      StepFailure when mb lookup command fails.
    """
    name = name or 'lookup GN args'
    additional_args = ['--recursive' if recursive else '--quiet']
    lookup_test_data = 'target_cpu = "x86"\nuse_remoteexec = true\n'
    result = self.run_mb_cmd(
        name,
        'lookup',
        source_dir,
        builder_id,
        mb_path=mb_path,
        mb_config_path=mb_config_path,
        chromium_config=chromium_config,
        phase=phase,
        android_version_code=android_version_code,
        android_version_name=android_version_name,
        additional_args=additional_args,
        stdout=self.m.raw_io.output_text(),
        step_test_data=lambda: self.m.raw_io.test_api.stream_output_text(
            lookup_test_data))

    gn_args = result.stdout
    reformatted_gn_args = self.m.gn.reformat_args(gn_args)
    self.m.gn.present_args(
        result,
        reformatted_gn_args,
        location=gn_args_location,
        max_text_lines=gn_args_max_text_lines)

    return gn_args

  @_with_chromium_layout
  def mb_gen(self,
             source_dir: Path,
             build_dir: Path,
             builder_id,
             *,
             name=None,
             mb_path=None,
             mb_config_path=None,
             isolated_targets=None,
             phase=None,
             android_version_code=None,
             android_version_name=None,
             gn_args_location=None,
             gn_args_max_text_lines=None,
             recursive_lookup=False,
             write_ide_json=False,
             **kwargs):
    """Generate the build files in the source tree.

    Args:
      source_dir: The path to the top-level repo.
      build_dir: The path to the directory containing built outputs.
      builder_id: The ID for the builder to generate build files for.
      name: The name of the step. If not provided 'generate_build_files' will be
        used.
      mb_path: The path to the source directory containing the mb.py script. If
        not provided, the subdirectory tools/mb within the source tree will be
        used.
      mb_config_path: The path to the configuration file containing the builder
        specifications to be used by mb. If not provided, the
        project_generator.config_path config value will be used. If that is
        falsey, then mb_config.pyl under the directory identified by mb_path
        will be used.
      gn_args_location: Controls where the GN args for the build should be
        presented. By default or if gn.DEFAULT, the args will be in step_text if
        the count of lines is less than gn_args_max_text_lines or the logs
        otherwise. To force the presentation to the step_text or logs, use
        gn.TEXT or gn.LOGS, respectively.
      gn_args_max_text_lines: The maximum number of lines of GN args to display
        in the step_text when using the default behavior for displaying GN args.
      recursive_lookup: Whether the lookup of the GN arguments should
        recursively expand imported args files.
      write_ide_json: A boolean indicating if mb should have gn generate
        a large JSON file containing target information for the project.

    Returns:
      The content of the args.gn file.
    """
    # Get the GN args before running any other steps so that if any subsequent
    # steps fail, developers will have the information about what the GN args
    # are so that they can reproduce the issue locally
    gn_args = self.mb_lookup(
        source_dir,
        builder_id,
        mb_path=mb_path,
        mb_config_path=mb_config_path,
        phase=phase,
        recursive=recursive_lookup,
        android_version_code=android_version_code,
        android_version_name=android_version_name,
        gn_args_location=gn_args_location,
        gn_args_max_text_lines=gn_args_max_text_lines)

    mb_args = ['--json-output', self.m.json.output(name="failure_summary")]

    step_test_data = (
        lambda: self.m.json.test_api.output({}, name='failure_summary'))

    mb_args.extend(self._mb_isolate_map_file_args(source_dir))

    if isolated_targets:
      sorted_isolated_targets = sorted(set(isolated_targets))
      # TODO(dpranke): Change the MB flag to '--isolate-targets-file', maybe?
      data = '\n'.join(sorted_isolated_targets) + '\n'
      mb_args += ['--swarming-targets-file', self.m.raw_io.input_text(data)]

    if write_ide_json:
      mb_args += ['--write-ide-json']

    mb_args.append(build_dir)

    additional_instructions = None
    if gn_args:
      additional_instructions = (
          '</br>*Note: The gn_args used in this gen:*<br/><li> ' +
          '</br><li> '.join([arg for arg in gn_args.split('\n') if arg]))

    mb_args.extend(self.m.chromium_rts.mb_args())
    name = name or 'generate_build_files'
    result = None
    try:
      with self.mb_failure_handler(name):
        result = self.run_mb_cmd(
            name,
            'gen',
            source_dir,
            builder_id,
            mb_path=mb_path,
            mb_config_path=mb_config_path,
            phase=phase,
            android_version_code=android_version_code,
            android_version_name=android_version_name,
            additional_args=mb_args,
            step_test_data=step_test_data,
            include_instruction=True,
            additional_instructions=additional_instructions,
            **kwargs)
    except self.m.step.StepFailure as f:
      result = f.result
      raise
    finally:
      if result:
        result.presentation.tags[self.BISECT_STEP_TAG] = 'true'

    if isolated_targets:
      result.presentation.logs['swarming-targets-file.txt'] = (
          sorted_isolated_targets)

    # gn_logs.txt contains debug info for vars with smart defaults. Display
    # its contents in the build for easy debugging.
    gn_logs_path = build_dir / 'gn_logs.txt'
    self.m.path.mock_add_paths(gn_logs_path)
    if self.m.path.exists(gn_logs_path):
      self.m.file.read_text('read gn_logs.txt', gn_logs_path)

    self.m.repro_instructions.update_invocation_instructions()
    return gn_args

  @_with_chromium_layout
  def mb_isolate_everything(
      self,
      source_dir: Path,
      build_dir: Path,
      builder_id,
      *,
      phase=None,
  ):
    """Generate .isolate files for all targets.

    Args:
      source_dir: The path to the top-level repo.
      build_dir: The path to the directory containing built outputs.
    """
    args = []

    args.extend(self._mb_isolate_map_file_args(source_dir))

    args.append(build_dir)

    name = 'generate .isolate files'
    self.run_mb_cmd(
        name,
        'isolate-everything',
        source_dir,
        builder_id,
        phase=phase,
        additional_args=args)

  @contextlib.contextmanager
  def mb_failure_handler(self, name):
    try:
      yield
    except self.m.step.StepFailure as ex:
      if ex.result.json.outputs:
        failure_summary = ex.result.json.outputs['failure_summary']
        if failure_summary and failure_summary['output']:
          ex.reason = self._format_failures(
              failure_summary['output'],
              name,
              footer='More information can be found in the stdout.')
      raise

  def get_build_target_arch(self):
    return {
        ('intel', 32): 'x86',
        ('intel', 64): 'x64',
        ('arm', 32): 'arm',
        ('arm', 64): 'arm64',
        ('mips', 32): 'mips',
        ('mips', 64): 'mips64',
        ('mipsel', 32): 'mipsel',
        ('mipsel', 64): 'mips64el',
    }[self.m.chromium.c.TARGET_ARCH, self.m.chromium.c.TARGET_BITS]
