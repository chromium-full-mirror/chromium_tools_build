# Copyright 2013 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import collections
import contextlib
import functools
import textwrap

from recipe_engine import recipe_api

from . import types as chromium
from .config import validate_config

from PB.recipe_engine import result as result_pb2
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb

_CR_COMPILE_GUARD_NAME = 'CR_COMPILE_GUARD.txt'
_CR_COMPILE_GUARD_CONTENTS = textwrap.dedent("""\
    This file exists while a build compiles and is removed at the end of
    compilation. If the next build finds that the file exists prior to
    compilation, it will wipe the output directory.

    See https://crbug.com/959436 for more context.
    """)


class ChromiumApi(recipe_api.RecipeApi):

  def __init__(self, input_properties, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self._build_properties = None
    self._version = None
    self._clang_version = None
    # TODO(yueshe@) - migrate this property to xcode module once downstream
    # no longer sets this property
    self._xcode_build_version = input_properties.xcode_build_version
    self._goma_cache_silo = input_properties.goma_cache_silo

  @property
  def xcode_build_version(self):
    return self._xcode_build_version

  def make_config_params(self, *args, **kwargs):
    config_object, params = super().make_config_params(*args, **kwargs)
    if config_object is not None:
      validate_config(config_object)
    return config_object, params

  def apply_config(self, config_name, config_object=None, optional=False):
    super().apply_config(config_name, config_object, optional)
    validate_config(config_object or self.c)

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
        'HOST_PLATFORM': self.m.platform.name,
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
        'CHECKOUT_PATH': self.m.path['checkout'],
        'TEST_ONLY': False,
    }

    return defaults

  def get_env(self):
    ret = {}
    if self.c.env.PATH:
      ret['PATH'] = self.m.path.pathsep.join([str(p) for p in self.c.env.PATH] +
                                             ['%(PATH)s'])
    if self.c.env.GOMA_STORE_ONLY:
      ret['GOMA_STORE_ONLY'] = self.c.env.GOMA_STORE_ONLY
    if self.c.env.FORCE_MAC_TOOLCHAIN:
      ret['FORCE_MAC_TOOLCHAIN'] = self.c.env.FORCE_MAC_TOOLCHAIN
    if self.c.env.GOMA_DEPS_CACHE_MAX_PROTO_SIZE_IN_MB:
      ret['GOMA_DEPS_CACHE_MAX_PROTO_SIZE_IN_MB'] = (
          self.c.env.GOMA_DEPS_CACHE_MAX_PROTO_SIZE_IN_MB)
    return ret

  @property
  def build_properties(self):
    return self._build_properties

  @property
  def output_dir(self):
    """Return the path to the built executable directory."""
    return self.c.build_dir.join(self.c.build_config_fs)

  @property
  def ninja_path(self):
    return self.m.path['checkout'].join('third_party', 'ninja', 'ninja')

  def get_version(self):
    """Returns a dictionary describing the version.

    The dictionary will map the name of the portion of the version to its
    numeric value e.g.
    { 'MAJOR'": '51', 'MINOR': '0', 'BUILD': '2704', 'PATCH': '0' }
    """
    if self._version is None:
      self._version = self.get_version_from_file(self.m.path['checkout'].join(
          'chrome', 'VERSION'))
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

    # TODO: crbug.com/358481 . The build_config should probably be a property
    # passed in from the slave config, but that doesn't exist today, so we
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
                       char_limit=700,
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
    if len(footer) <= 2 * AVG_LINE_SIZE:
      summary_lines.append('#### %s' % footer)

    return '\n'.join(summary_lines)

  def _run_ninja(self, ninja_command, name=None, ninja_env=None, **kwargs):
    """
    Run ninja with given command and env.

    Args:
      ninja_command: Command used for build.
                     This is sent as part of log.
                     (e.g. ['ninja', '-C', 'out/Release'])
      name: Name of compile step.
      ninja_env: Environment for ninja.

    Returns:
      A named tuple with the fields
        - failure_summary: string of the error that occurred during the step,
        - retcode: return code of the step

    Raises:
      InfraFailure from compile step
      StepFailure from compile confirm no-op step
    """

    CompileResult = collections.namedtuple('CompileResult',
                                           'failure_summary retcode')

    cmd = [
        'vpython3',
        self.resource('ninja_wrapper.py'),
        '--ninja_info_output',
        self.m.json.output(add_json_log='on_failure', name='ninja_info'),
        '--failure_output',
        self.m.raw_io.output_text(
            add_output_log='on_failure', name='failure_summary'),
    ]
    if kwargs.get('no_prune_venv'):
      kwargs.pop('no_prune_venv')
      cmd.append('--no_prune_venv')
    cmd.append('--')
    cmd.extend(ninja_command)

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
    try:
      with self.m.context(env=ninja_env):
        ninja_step_result = self.m.step(
            name or 'compile', cmd, step_test_data=step_test_data, **kwargs)
    except self.m.step.StepFailure as ex:
      ninja_step_result = ex.result
      if ninja_step_result.retcode != 1:
        raise self.m.step.InfraFailure(
            ninja_step_result.name, result=ninja_step_result)

      failure_summary = ('(retcode=%d) No failure summary provided.' %
                         ninja_step_result.retcode)
      if ninja_step_result.raw_io.output_text:
        failure_summary = ninja_step_result.raw_io.output_text

      return CompileResult(
          failure_summary=failure_summary, retcode=ninja_step_result.retcode)

    finally:
      if not self.m.runtime.in_global_shutdown:
        clang_crashreports_script = self.m.path['checkout'].join(
            'tools', 'clang', 'scripts', 'process_crashreports.py')
        if self.m.path.exists(clang_crashreports_script):
          source = '%s-%s' % (self.m.builder_group.for_current,
                              self.m.buildbucket.builder_name)
          if self.m.buildbucket.build.number:
            source += '-%s' % self.m.buildbucket.build.number
          self.m.step(
              'process clang crashes',
              ['python3', clang_crashreports_script, '--source', source],
              **kwargs)

    ninja_command_explain = ninja_command + ['-d', 'explain', '-n']

    ninja_no_work = 'ninja: no work to do.'

    # Once we've compiled once, a second attempt to compile should do nothing.
    # Any actual work we do here indicates that the dependency graph in GN is
    # misconfigured somehow, which is bad and should break the build.
    with self.m.context(env=ninja_env):
      step_result = self.m.step(
          (name or 'compile') + ' confirm no-op',
          ninja_command_explain,
          stdout=self.m.raw_io.output_text(),
          step_test_data=(
              lambda: self.m.raw_io.test_api.stream_output_text(ninja_no_work)))

    if ninja_no_work in step_result.stdout:
      # No dependency issue found.
      return CompileResult(
          failure_summary='No dependency issues found',
          retcode=ninja_step_result.exc_result.retcode)

    step_result.presentation.step_text = (
        "This should have been a no-op, but it wasn't.")

    step_result.presentation.status = self.m.step.FAILURE
    return CompileResult(
        failure_summary=textwrap.dedent("""
            Failing build because ninja reported work to do.
            This means that after completing a compile, another was run and
            it resulted in still having work to do (that is, a no-op build
            wasn't a no-op). Consult the first "ninja explain:" line for a
            likely culprit.
         """).strip(),
        retcode=1)

  def _run_ninja_with_goma(self,
                           ninja_command,
                           ninja_env,
                           name=None,
                           ninja_log_outdir=None,
                           ninja_log_compiler=None,
                           goma_env=None,
                           **kwargs):
    """
    Run ninja with goma.
    This function start goma, call _run_ninja and stop goma using goma module.

    Args:
      ninja_command: Command used for build.
                     This is sent as part of log.
                     (e.g. ['ninja', '-C', 'out/Release'])
      ninja_env: Environment for ninja.
      name: Name of compile step.
      ninja_log_outdir: Directory of ninja log. (e.g. "out/Release")
      ninja_log_compiler: Compiler used in ninja. (e.g. "clang")
      goma_env: Environment controlling goma behavior.

    Returns:
      A named tuple with the fields
        - failure_summary: string of the error that occurred during the step,
        - retcode: return code of the step

    Raises:
      - InfraFailure when an unexpected goma failure occurs
    """
    # TODO(martiniss): This is a terrible hack and needs to be removed. See
    # https://crbug.com/984451 for more information
    if not self.c.compile_py.prune_venv:
      kwargs['no_prune_venv'] = True

    build_exit_status = None

    self.m.goma.start(goma_env)

    if not self.c.compile_py.goma_use_local and not self.m.platform.is_mac:
      # Do not allow goma to invoke local compiler.
      # However, it is disabled on mac because GOMA_USE_LOCAL=false makes mac
      # builders hangs. Please see crbug.com/1056935.
      ninja_env['GOMA_USE_LOCAL'] = 'false'
    if self.c.compile_py.goma_enable_cache_silo or self._goma_cache_silo:
      ninja_env['RBE_cache_silo'] = self.m.buildbucket.builder_name
    build_exit_status = -1
    try:
      ninja_result = self._run_ninja(ninja_command, name, ninja_env, **kwargs)
      build_exit_status = ninja_result.retcode

    finally:
      if not self.m.runtime.in_global_shutdown:
        self.m.goma.stop(
            ninja_log_outdir=ninja_log_outdir,
            ninja_log_compiler=ninja_log_compiler,
            ninja_log_command=ninja_command,
            build_exit_status=build_exit_status,
            build_step_name=name)
    return ninja_result

  def _run_ninja_with_reclient(self,
                               ninja_command,
                               ninja_env,
                               name=None,
                               **kwargs):
    """
    Run ninja with reclient.
    This function starts reclient, calls _run_ninja and stops reclient.

    Args:
      ninja_command: Command used for build.
                     This is sent as part of log.
                     (e.g. ['ninja', '-C', 'out/Release'])
      ninja_env: Environment for ninja.
      name: Name of the compile step.

    Returns:
      A named tuple with the fields
        - failure_summary: string of the error that occurred during the step,
        - retcode: return code of the step

    Raises:
      - InfraFailure when an unexpected reclient failure occurs
    """
    with self.m.reclient.process(
        name, ninja_command,
        self.c.compile_py.reclient_deps_cache_by_step) as p:
      ninja_result = self._run_ninja(ninja_command, name, ninja_env, **kwargs)
      p.build_exit_status = ninja_result.retcode
    return ninja_result

  def _run_ninja_without_remote(self,
                                ninja_command,
                                ninja_log_outdir,
                                name=None,
                                ninja_env=None,
                                **kwargs):
    """
    Run ninja and uploads the ninja logs.

    Args:
      ninja_command: Command used for build.
                     This is sent as part of log.
                     (e.g. ['ninja', '-C', 'out/Release'])
      ninja_log_outdir: Directory of ninja log. (e.g. "out/Release")
      name: Name of compile step.
      ninja_env: Environment for ninja.

    Returns:
      A named tuple with the fields
        - failure_summary: string of the error that occurred during the step,
        - retcode: return code of the step

    Raises:
      InfraFailure from compile step
      StepFailure from compile confirm no-op step
    """
    compile_exit_status = 1
    try:
      ninja_result = self._run_ninja(
          ninja_command=ninja_command,
          name=name or 'compile',
          ninja_env=ninja_env,
          **kwargs)
      compile_exit_status = ninja_result.retcode
      return ninja_result
    except self.m.step.StepFailure as ex:
      compile_exit_status = ex.retcode
      raise ex
    finally:
      upload_ninja_log_args = [
          '--gsutil-py-path', self.m.depot_tools.gsutil_py_path,
          '--skip-sendgomatsmon', '--ninja-log-outdir', ninja_log_outdir,
          '--ninja-log-command-file',
          self.m.json.input(ninja_command), '--build-exit-status',
          compile_exit_status, '--ninja-log-compiler',
          self.c.compile_py.compiler or 'unknown'
      ]
      self.m.step(
          name='upload_ninja_log',
          cmd=[
              'vpython3',
              self.repo_resource('recipes', 'upload_goma_logs.py')
          ] + upload_ninja_log_args)

  @contextlib.contextmanager
  def guard_compile(self, suffix=''):
    """Ensure that the output directory gets cleaned if compile is interrupted.

    On entry, this context manager checks for the existence of a sentinel file
    inside the output directory, cleaning the output directory if it's present.
    It then creates the sentinel file.

    On orderly, non-exception exit, this context manager removes the sentinel
    file.

    The result of using this context manager will be that, if something
    untoward happens while in scope -- build cancellation, unexpected infra
    failure, etc -- the output directory will be clobbered during the next
    build.
    """
    guard_path = self.m.chromium.output_dir.join(_CR_COMPILE_GUARD_NAME)
    if self.m.path.exists(guard_path):
      self.m.file.rmtree('remove unreliable output dir' + suffix,
                         self.m.chromium.output_dir)
    self.m.file.ensure_directory('ensure output directory' + suffix,
                                 self.m.chromium.output_dir)
    self.m.file.write_text('create compile guard' + suffix, guard_path,
                           _CR_COMPILE_GUARD_CONTENTS)
    yield
    self.m.file.remove('remove compile guard' + suffix, guard_path)

  # TODO(tikuta): Remove use_goma_module.
  # Decrease the number of ways configuring with or without goma.
  @_with_chromium_layout
  def compile(self,
              targets=None,
              name=None,
              out_dir=None,
              target=None,
              use_goma_module=False,
              use_reclient=False,
              **kwargs):
    """Return a compile.py invocation.

    Args:
      targets: List of build targets to compile. If empty, compile targets will
        be taken from chromium.c.compile_py.default_targets. If 'all' is one of
        the targets, no targets will appear on the ninja command line, which
        invokes ninja's behavior to build all targets that do not appear as an
        input to another target.
      name: Name of compile step.
      out_dir: Output directory for the compile.
      target: Custom config name to use in the output directory (defaults to
        "Release" or "Debug").
      use_goma_module (bool): If True, use the goma recipe module.
      use_reclient (bool): If True, use reclient as the remote compiler.

    Returns:
      A RawResult object with the compile step's status and failure message

    Raises:
      - InfraFailure when goma failure occurs
    """
    targets = targets or self.c.compile_py.default_targets.as_jsonish()
    assert isinstance(targets, (list, tuple)), type(targets)
    assert not (use_goma_module and use_reclient
               ), 'goma and reclient cannot be enabled at the same time'

    if self.c.use_gyp_env and self.c.gyp_env.GYP_DEFINES.get('clang', 0) == 1:
      # Get the Clang revision before compiling.
      self._clang_version = self.get_clang_version()

    ninja_env = self.get_env()
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
            ('%(PATH)s',
             str(self.m.path.dirname(self.ninja_path))))

    if out_dir is None:
      out_dir = 'out'

    target_output_dir = self.m.path.join(self.m.path['checkout'], out_dir,
                                         target or self.c.build_config_fs)
    target_output_dir = self.m.path.abspath(target_output_dir)

    command = [str(self.ninja_path), '-w', 'dupbuild=err', '-C', target_output_dir]

    if self.c.compile_py.show_ninja_stats:
      command.extend(['-d', 'stats'])

    if self.c.compile_py.build_args:
      command.extend(self.c.compile_py.build_args)

    if use_goma_module:
      goma_env = ninja_env.copy()
      goma_env['GOMA_CACHE_DIR'] = self.m.goma.default_cache_path

      # Enable goma DepsCache
      goma_env['GOMA_DEPS_CACHE_FILE'] = "goma_deps_cache"

      if self.c.compile_py.goma_hermetic:
        goma_env['GOMA_HERMETIC'] = self.c.compile_py.goma_hermetic

      if self.c.compile_py.goma_enable_global_file_stat_cache:
        goma_env['GOMA_ENABLE_GLOBAL_FILE_STAT_CACHE'] = 'true'

      if self.c.compile_py.goma_max_active_fail_fallback_tasks:
        goma_env['GOMA_MAX_ACTIVE_FAIL_FALLBACK_TASKS'] = (
            self.c.compile_py.goma_max_active_fail_fallback_tasks)
      if (self.m.tryserver.is_tryserver or self.c.compile_py.goma_failfast):
        # We rely on goma to meet cycle time goals on the tryserver. It's better
        # to fail early.
        goma_env['GOMA_FAIL_FAST'] = 'true'
      else:
        goma_env['GOMA_ALLOWED_NETWORK_ERROR_DURATION'] = '1800'
      # TODO(tikuta): Remove this and let goma module set '-j'
      #               inside build_with_goma.
      # The right way to configure goma jobs number is in cr-buildbucket.cfg.
      # See also doc for goma.jobs.
      command += ['-j', self.m.goma.jobs]
      if self.m.goma.debug:
        ninja_env['GOMA_DUMP'] = '1'

    if use_reclient:
      command += ['-j', self.m.reclient.jobs]

    if targets is not None and 'all' not in targets:
      command += targets

    assert 'env' not in kwargs
    assert 'cwd' not in kwargs

    try:
      with self.m.context(cwd=self.m.context.cwd or self.m.path['checkout']):
        if use_goma_module:
          ninja_result = self._run_ninja_with_goma(
              ninja_command=command,
              ninja_env=ninja_env,
              name=name or 'compile',
              goma_env=goma_env,
              ninja_log_outdir=target_output_dir,
              ninja_log_compiler=self.c.compile_py.compiler or 'goma',
              **kwargs)
        elif use_reclient:
          ninja_result = self._run_ninja_with_reclient(
              ninja_command=command,
              ninja_env=ninja_env,
              name=name or 'compile',
              **kwargs)
        else:
          ninja_result = self._run_ninja_without_remote(
              ninja_command=command,
              ninja_log_outdir=target_output_dir,
              name=name or 'compile',
              ninja_env=ninja_env,
              **kwargs)
    except self.m.step.StepFailure as ex:
      # If there is an infra failure raised at this point that means
      # goma did not get to start, so no need to handle it
      if ex.retcode != 1 or not use_goma_module:
        raise ex
      # Goma failure
      return self._handle_goma_failures(ex.reason)

    if ninja_result.retcode:
      failure_summary = self._format_failures(
          ninja_result.failure_summary, name or 'compile',
          'More information in raw_io.output_text[failure_summary]')
      if use_goma_module:
        # It's possible for the StepFailure of the compile step to have
        # a goma failure, so to avoid the message getting repeated it
        # will be handled here
        return self._handle_goma_failures(failure_summary)
      return result_pb2.RawResult(
          status=common_pb.FAILURE, summary_markdown=failure_summary)
    return result_pb2.RawResult(status=common_pb.SUCCESS)

  def _handle_goma_failures(self, failure_summary):
    failure_result_code = ''

    json_status = self.m.goma.jsonstatus['notice'][0]

    if not json_status.get('infra_status'):
      failure_result_code = 'GOMA_SETUP_FAILURE'
    elif json_status['infra_status']['ping_status_code'] != 200:
      failure_result_code = 'GOMA_PING_FAILURE'
    elif json_status['infra_status'].get('num_user_error', 0) > 0:
      failure_result_code = 'GOMA_BUILD_ERROR'

    if failure_result_code:
      assert len(failure_result_code) <= 20
      # FIXME(yyanagisawa): mark the active step exception on goma error.
      #
      # This is workaround to make goma error recognized as infra exception.
      # 1. even if self.m.step.InfraFailure is raised, the step is not shown
      #    as EXCEPTION step in milo.  We need to make status EXCEPTION to
      #    make the step annotated as STEP_EXCEPTION. (crbug.com/856914)
      # 2. I believe it natural to mark compile step exception but we cannot.
      #    since this step is executed after compile step, it is recognized as
      #    finalized step, and we cannot edit such a step.  Let us touch
      #    active result instead.
      #    However, if we pick the active step, the last step of
      #    'postprocess_goma' would be chosen, and it is confusing.
      #    Let us create a fake step to represent the case.
      #    It might be better than both not showing exception and marking
      #    'stop cloudtail' as exception.
      fake_step = self.m.step('infra status', [])
      fake_step.presentation.status = self.m.step.EXCEPTION
      fake_step.presentation.step_text = failure_result_code
      props = fake_step.presentation.properties
      props['extra_result_code'] = [failure_result_code]
      raise self.m.step.InfraFailure('Infra compile failure: %s' %
                                     failure_summary)
    return result_pb2.RawResult(
        status=common_pb.FAILURE, summary_markdown=failure_summary)

  @_with_chromium_layout
  def runtest(self,
              test,
              args=None,
              xvfb=False,
              name=None,
              builder_group=None,
              parse_gtest_output=False,
              test_type=None,
              python_mode=False,
              test_launcher_summary_output=None,
              **kwargs):
    """Return a runtest.py invocation."""
    args = args or []
    assert isinstance(args, collections.Sequence), repr(args)

    t_name, ext = self.m.path.splitext(self.m.path.basename(test))
    if not python_mode and self.m.platform.is_win and ext == '':
      test += '.exe'

    full_args = ['--target', self.c.build_config_fs]
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
    full_args.append('--slave-name=%s' % self.m.properties['bot_id'])
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
    if self.c.runtests.enable_memcheck:
      full_args.extend([
          '--pass-build-dir',
          '--pass-target',
          '--run-shell-script',
          self.c.runtests.memory_tests_runner,
          '--test',
          t_name,
          '--tool',
          'memcheck',
      ])
    else:
      full_args.append(test)

    full_args.extend(args)

    runtest_path = self.repo_resource('recipes', 'runtest.py')
    # We need this, as otherwise runtest.py fails due to expecting the cwd to
    # be the checkout, when instead it's kitchen-workdir. We also can't use
    # self.m.path['checkout'] since that has an extra '/src' added onto it
    # compared to what runtest.py expects.
    with self.m.context(cwd=self.m.path['cache'].join('builder')):
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
  def get_clang_version(self, **kwargs):
    with self.m.context(env=self.get_env()):
      args = [
          '--src-dir', self.m.path['checkout'], '--output-json',
          self.m.json.output()
      ]
      if self.c.use_tot_clang:
        args.append('--use-tot-clang')
      step_result = self.m.step(
          name='clang_revision',
          cmd=['python3', self.resource('clang_revision.py')] + args,
          step_test_data=lambda: self.m.json.test_api.output(
              {'clang_revision': '123456-7'}),
          **kwargs)
      clang_revision = step_result.json.output['clang_revision']
      step_result.presentation.properties['clang_revision'] = clang_revision
    return clang_revision

  def ensure_goma(self, client_type='release'):
    self.c.compile_py.goma_dir = self.m.goma.ensure_goma(
        client_type=client_type)

  def get_mac_toolchain_installer(self):
    assert self.c.mac_toolchain.installer_cipd_package
    assert self.c.mac_toolchain.installer_version
    assert self.c.mac_toolchain.installer_cmd

    cipd_root = self.m.path['start_dir']
    cipd_pkg = self.c.mac_toolchain.installer_cipd_package
    pkg_version = self.c.mac_toolchain.installer_version
    cmd = self.c.mac_toolchain.installer_cmd
    self.m.cipd.ensure(
        cipd_root,
        self.m.cipd.EnsureFile().add_package(cipd_pkg, pkg_version))
    return cipd_root.join(cmd)

  # TODO(crbug.com/797051): remove this when the old "hermetic" flow is
  # no longer used.
  def delete_old_mac_toolchain(self):
    """Remove the old "hermetic" toolchain cache.

    This is to expose any lingering dependencies on the old cache.
    """
    old_cache = self.m.path['checkout'].join(
        'build', '%s_files' % self.m.chromium.c.TARGET_PLATFORM)
    self.m.file.rmtree('delete deprecated Xcode cache', old_cache)

  def ensure_mac_toolchain(self, checkout_dir=None):
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
    xcode_app_path = self.m.path['cache'].join('xcode_%s_%s.app' %
                                               (kind, xcode_build_version))

    with self.m.step.nest('ensure xcode') as step_result:
      step_result.presentation.step_text = (
          'Ensuring Xcode version %s in %s' %
          (xcode_build_version, xcode_app_path))

      self.delete_old_mac_toolchain()

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

  def ensure_toolchains(self, checkout_dir=None):
    if self.c.HOST_PLATFORM == 'mac':
      self.ensure_mac_toolchain(checkout_dir)

  def clobber_if_needed(self):
    """Add an explicit clobber step if requested."""
    # clobber_before_runhooks is true for bots that apply the 'clobber' config,
    # that is for bots that do clobber bots on every build.
    # properties.get('clobber') is true on bots that normally don't clobber,
    # when the "Clobber" button in the buildbot UI is pressed.
    if (self.c.clobber_before_runhooks or
        self.m.properties.get('clobber') is not None):
      self.m.file.rmtree('clobber', self.output_dir)

      # A CrOS-side change got rolled into m108 that makes a dir in
      # //build/cros_cache/ read-only. This interferes with the CrOS SDK when
      # it tries to redownload itself. So we forcibly clobber the dir here
      # to let runhooks proceed safely.
      # TODO(b/256012263): Remove this when the fix has been merged into m108.
      with self.m.step.nest('workaround for read-only //build/cros_cache/ dir'):
        cros_cache = self.m.path['checkout'].join('build', 'cros_cache')
        if self.m.path.exists(cros_cache):
          self.m.step('chmod cros_cache', ['chmod', '-R', '744', cros_cache])
          self.m.file.rmtree('clobber cros_cache', cros_cache)

  @_with_chromium_layout
  def runhooks(self, env=None, **kwargs):
    """Run the build-configuration hooks for chromium."""

    # runhooks might write things into the output directory, so clobber before
    # that.
    self.clobber_if_needed()

    runhooks_env = self.get_env()
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

    with self.m.gsutil.configure_gsutil():
      with self.m.context(env=runhooks_env):
        self.m.gclient.runhooks(**kwargs)

  @_with_chromium_layout
  def run_gn(self,
             use_goma=False,
             gn_path=None,
             build_dir=None,
             use_reclient=False,
             **kwargs):
    assert not use_goma or not use_reclient
    if not gn_path:
      gn_path = self.m.depot_tools.gn_py_path

    gn_args = list(self.c.gn_args)

    # TODO(dpranke): Figure out if we should use the '_x64' thing to
    # consistent w/ GYP, or drop it to be consistent w/ the other platforms.
    build_dir = build_dir or '//out/%s' % self.c.build_config_fs

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
    }.get((self.c.TARGET_ARCH, self.c.TARGET_BITS))
    if gn_cpu:
      gn_args.append('target_cpu="%s"' % gn_cpu)

    gn_env = self.get_env()
    # TODO: crbug.com/395784.
    # Consider getting the flags to use via the project_generator config
    # and/or modifying the goma config to modify the gn flags directly,
    # rather than setting the gn_args flags via a parameter passed to
    # run_gn(). We shouldn't have *three* different mechanisms to control
    # what args to use.
    if use_goma:
      gn_args.append('use_goma=true')
      gn_args.append('goma_dir="%s"' % self.c.compile_py.goma_dir)

      # Do not allow goma to invoke local compiler.
      gn_env['GOMA_USE_LOCAL'] = 'false'

    if use_reclient:
      gn_args.append('use_remoteexec=true')

    gn_args.extend(self.c.project_generator.args)

    cmd = [
        gn_path,
        '--root=%s' % str(self.m.path['checkout']),
        'gen',
        build_dir,
        '--args=%s' % ' '.join(gn_args),
    ]
    if str(gn_path).endswith('.py'):
      cmd = ['python3'] + cmd
    with self.m.context(
        cwd=kwargs.get('cwd', self.m.path['checkout']), env=gn_env):
      self.m.step(name='gn', cmd=cmd, **kwargs)

  def _mb_isolate_map_file_args(self):
    for isolate_map_path in self.c.project_generator.isolate_map_paths:
      yield '--isolate-map-file'
      yield isolate_map_path

  def _mb_build_dir_args(self, build_dir):
    if not build_dir:
      build_dir = '//out/%s' % self.c.build_config_fs
    return [build_dir]

  @_with_chromium_layout
  def run_mb_cmd(self,
                 name,
                 mb_command,
                 builder_id,
                 mb_path=None,
                 mb_config_path=None,
                 chromium_config=None,
                 phase=None,
                 use_goma=True,
                 android_version_code=None,
                 android_version_name=None,
                 additional_args=None,
                 **kwargs):
    """Run an arbitrary mb command.

    Args:
      name: The name of the step.
      mb_command: The mb command to run.
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
      use_goma: Whether goma is needed or not. If use_goma=True but not yet
        installed, it will run ensure_goma to install goma client.
      additional_args: Any args to the mb script besides those for setting the
        group, builder and the path to the config file.
      **kwargs: Additional arguments to be forwarded onto the python API.
    """
    chromium_config = chromium_config or self.c

    mb_path = mb_path or self.m.path['checkout'].join('tools', 'mb')
    mb_config_path = (
        mb_config_path or chromium_config.project_generator.config_path or
        self.m.path.join(mb_path, 'mb_config.pyl'))

    args = [
        mb_command,
        '-m',
        builder_id.group,
        '-b',
        builder_id.builder,
        '--config-file',
        mb_config_path,
    ]

    if phase is not None:
      args += ['--phase', str(phase)]

    # self.c instead of chromium_config is not a mistake here, if we have
    # already ensured goma, we don't need to do it for this config object
    goma_dir = self.c.compile_py.goma_dir
    # TODO(gbeaty): remove this weird goma fallback or cover it
    if use_goma and not goma_dir:  # pragma: no cover
      # This method defaults to use_goma=True, which doesn't necessarily
      # match build-side configuration. However, MB is configured
      # src-side, and so it might be actually using goma.
      self.ensure_goma()
      goma_dir = self.c.compile_py.goma_dir
    if goma_dir:
      args += ['--goma-dir', goma_dir]

    if android_version_code:
      args += ['--android-version-code=%s' % android_version_code]
    if android_version_name:
      args += ['--android-version-name=%s' % android_version_name]
    # TODO(crbug.com/1060857): Remove this once swarming task templates
    # support command prefixes.
    if self.c.project_generator.use_luci_auth:
      args += ['--luci-auth']

    combined_args = args + (additional_args or [])

    cmd = ['python3', '-u', mb_path.join('mb.py')] + combined_args

    # If an environment was provided, copy it so that we don't modify the
    # caller's data
    # This runs with an almost-bare env being passed along, so we get a clean
    # environment without any GYP_DEFINES being present to cause confusion.
    env = self.get_env()

    if use_goma:
      # Do not allow goma to invoke local compiler.
      # GOMA_USE_LOCAL is passed to gomacc from ninja.
      # And in windows, env var for ninja is specified in `gn gen` step.
      # We don't need to disallow local compile,
      # but we want to utilize remote cpu resource more.
      env['GOMA_USE_LOCAL'] = 'false'

    env.update(self.m.context.env)

    with self.m.context(
        # TODO(phajdan.jr): get cwd from context, not kwargs.
        cwd=kwargs.get('cwd', self.m.path['checkout']),
        env=env):
      return self.m.step(name, cmd, **kwargs)

  @_with_chromium_layout
  def mb_analyze(self,
                 builder_id,
                 analyze_input,
                 name=None,
                 mb_path=None,
                 mb_config_path=None,
                 chromium_config=None,
                 build_dir=None,
                 phase=None,
                 test_analyze_output=None,
                 **kwargs):
    """Determine which targets need to be built and tested.

    Args:
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
    mb_args.extend(self._mb_isolate_map_file_args())
    mb_args.extend(self._mb_build_dir_args(build_dir))
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
          builder_id,
          mb_path=mb_path,
          mb_config_path=mb_config_path,
          chromium_config=chromium_config,
          phase=phase,
          # Ignore no remote execution for analysis.
          use_goma=False,
          additional_args=mb_args,
          step_test_data=step_test_data,
          **kwargs)

  @_with_chromium_layout
  def mb_lookup(self,
                builder_id,
                name=None,
                mb_path=None,
                mb_config_path=None,
                recursive=False,
                chromium_config=None,
                phase=None,
                use_goma=True,
                use_reclient=False,
                android_version_code=None,
                android_version_name=None,
                gn_args_location=None,
                gn_args_max_text_lines=None):
    """Lookup the GN args for the build.

    Args:
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
    """
    name = name or 'lookup GN args'
    additional_args = ['--recursive' if recursive else '--quiet']
    lookup_test_data = ('goma_dir = "/b/build/slave/cache/goma_client"\n'
                        'target_cpu = "x86"\n')
    if use_goma:
      lookup_test_data += 'use_goma = true\n'
    elif use_reclient:
      lookup_test_data += 'use_remoteexec = true\n'
    result = self.run_mb_cmd(
        name,
        'lookup',
        builder_id,
        mb_path=mb_path,
        mb_config_path=mb_config_path,
        chromium_config=chromium_config,
        phase=phase,
        use_goma=use_goma,
        android_version_code=android_version_code,
        android_version_name=android_version_name,
        additional_args=additional_args,
        raise_on_failure=False,
        stdout=self.m.raw_io.output_text(),
        step_test_data=lambda: self.m.raw_io.test_api.stream_output_text(
            lookup_test_data))

    gn_args = result.stdout
    if gn_args is not None:
      reformatted_gn_args = self.m.gn.reformat_args(gn_args)
      self.m.gn.present_args(
          result,
          reformatted_gn_args,
          location=gn_args_location,
          max_text_lines=gn_args_max_text_lines)

    return gn_args

  @_with_chromium_layout
  def mb_gen(self,
             builder_id,
             name=None,
             mb_path=None,
             mb_config_path=None,
             use_goma=True,
             use_reclient=False,
             isolated_targets=None,
             build_dir=None,
             phase=None,
             android_version_code=None,
             android_version_name=None,
             gn_args_location=None,
             gn_args_max_text_lines=None,
             recursive_lookup=False,
             rts_setting=None,
             rts_recall=None,
             **kwargs):
    """Generate the build files in the source tree.

    Args:
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
      rts_setting - A string indicating which regression test selection model
        to use. None will disable RTS (bit.ly/chromium-rts)
      rts_recall - A float from (0 to 1] indicating what change recall rts
        should aim for, 0 being the fastest and 1 being the safest, and
        typically between .9 and 1

    Returns:
      The content of the args.gn file.
    """
    # Get the GN args before running any other steps so that if any subsequent
    # steps fail, developers will have the information about what the GN args
    # are so that they can reproduce the issue locally
    gn_args = self.mb_lookup(
        builder_id,
        mb_path=mb_path,
        mb_config_path=mb_config_path,
        phase=phase,
        use_goma=use_goma,
        use_reclient=use_reclient,
        recursive=recursive_lookup,
        android_version_code=android_version_code,
        android_version_name=android_version_name,
        gn_args_location=gn_args_location,
        gn_args_max_text_lines=gn_args_max_text_lines)

    mb_args = ['--json-output', self.m.json.output(name="failure_summary")]

    step_test_data = (
        lambda: self.m.json.test_api.output({}, name='failure_summary'))

    mb_args.extend(self._mb_isolate_map_file_args())

    if isolated_targets:
      sorted_isolated_targets = sorted(set(isolated_targets))
      # TODO(dpranke): Change the MB flag to '--isolate-targets-file', maybe?
      data = '\n'.join(sorted_isolated_targets) + '\n'
      mb_args += ['--swarming-targets-file', self.m.raw_io.input_text(data)]

    mb_args.extend(self._mb_build_dir_args(build_dir))
    if rts_setting:
      mb_args += ['--rts', rts_setting]

      if rts_recall:
        mb_args += ['--rts-target-change-recall', str(rts_recall)]

    name = name or 'generate_build_files'
    with self.mb_failure_handler(name):
      result = self.run_mb_cmd(
          name,
          'gen',
          builder_id,
          mb_path=mb_path,
          mb_config_path=mb_config_path,
          phase=phase,
          use_goma=use_goma,
          android_version_code=android_version_code,
          android_version_name=android_version_name,
          additional_args=mb_args,
          step_test_data=step_test_data,
          **kwargs)

      if isolated_targets:
        result.presentation.logs['swarming-targets-file.txt'] = (
            sorted_isolated_targets)

    return gn_args

  @_with_chromium_layout
  def mb_isolate_everything(self, builder_id, build_dir=None, phase=None):
    args = []

    args.extend(self._mb_isolate_map_file_args())

    args.extend(self._mb_build_dir_args(build_dir))

    name = 'generate .isolate files'
    self.run_mb_cmd(
        name,
        'isolate-everything',
        builder_id,
        phase=phase,
        use_goma=True,
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

  def taskkill(self):
    self.m.step(
        'taskkill',
        ['python3',
         self.repo_resource('recipes', 'kill_processes.py')],
        infra_step=True)

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
