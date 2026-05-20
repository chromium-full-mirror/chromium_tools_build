# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""API for interacting with the siso, experimental build tool."""

from __future__ import annotations

import contextlib
import re

from recipe_engine import recipe_api
from recipe_engine import step_data
from recipe_engine.config_types import Path

from RECIPE_MODULES.build.chromium_utr.instruction import get_utr_compile_instruction

# GCS bucket for Siso reports.
_GS_BUCKET = 'chrome-build-logs'

_TIME_CMD = '/usr/bin/time'

# Name of git footer for setting value of -k
_MAX_COMPILE_FAILURES = 'Max-Compile-Failures'
_CHECK_DEPS = 'Check-Deps'

# Resource usage format for time command.
# See also the documents of time and getrusage:
# https://www.man7.org/linux/man-pages/man1/time.1.html#:~:text=U%0A%20%20%20%20%20%20%20%20%20%20%20sys%20%25S-,The%20format%20string,-The%20format%20is
# https://man7.org/linux/man-pages/man2/getrusage.2.html
# Note that the following fields are defined by this recipe because they don't exist in rusage struct:
# - ru_rtime: Elapsed real time (in seconds).
# - ru_avgrss: Average resident set size of the process, in Kbytes.
# - ru_itrss: Average total (data+stack+text) memory use of the process, in Kbytes.
# - page_size: System's page size, in bytes.  This is a per-system constant, but varies between systems.
RUSAGE_FORMAT = """
{
  "ru_utime": %U,
  "ru_stime": %S,
  "ru_rtime": %e,
  "ru_maxrss": %M,
  "ru_avgrss": %t,
  "ru_ixrss": %X,
  "ru_idrss": %D,
  "ru_isrss": %p,
  "ru_itrss": %K,
  "ru_minflt": %R,
  "ru_majflt": %F,
  "ru_nswap": %W,
  "ru_inblock": %I,
  "ru_oublock": %O,
  "ru_msgsnd": %s,
  "ru_msgrcv": %r,
  "ru_nsignals": %k,
  "ru_nvcsw": %w,
  "ru_nivcsw": %c,
  "page_size": %Z
}
"""


class SisoApi(recipe_api.RecipeApi):
  """A module for interacting with siso."""

  def __init__(self, props, **kwargs):
    super().__init__(**kwargs)
    self._props = props
    self._disabled = False

  @property
  def enabled(self):
    """True if siso is enabled."""
    return self._props.project and not self._disabled

  @property
  def project(self):
    return self._props.project

  @property
  def reapi_instance(self):
    return self._props.reapi_instance

  @property
  def remote_jobs(self):
    if self._props.remote_jobs:
      return self._props.remote_jobs
    return min(20 * self.m.platform.cpu_count, 200)

  @contextlib.contextmanager
  def disable(self):
    """Disable Siso module during the context."""
    orig = self._disabled
    self._disabled = True
    yield
    self._disabled = orig

  def run_ninja(self,
                source_dir: Path,
                ninja_command,
                *,
                ninja_env=None,
                name='compile',
                siso_args=None,
                post_step_func=None,
                skip_log_upload=False,
                resource_usage_output_file=None,
                ninja_invocation_id=None,
                include_utr_instruction=False,
                builder_id=None,
                **kwargs):
    """Run the ninja command with siso.

        Args:
          ninja_command: Command used for build.
                     e.g. ['ninja', '-C', 'out/Release'],
          ninja_env: Environment for ninja.
          name: Name of compile step.
          siso_args: siso arguments.
          post_step_func: a function that runs on the step result.
          skip_log_upload: When true skip log.
          resource_usage_output_file: File which if provided will
            record the resource usage stats related to build step.
          ninja_invocation_id: ID of the build invocation.
          include_utr_instruction: Whether or not to include UTR reproduction
            instructions
          builder_id: ID for the builder compiling the targets.

        Returns:
          step_data.StepData of the build step.

        Raises:
          - InfraFailure when an unexpected failure occured.
    """
    assert self.enabled, 'siso is not enabled'
    self._assert_ninja_command(ninja_command)

    cmd = []

    if resource_usage_output_file:
      cmd.extend([
          _TIME_CMD,
          f'--format={RUSAGE_FORMAT}',
          '-o',
          resource_usage_output_file,
      ])

    ninja_dir = self._ninja_dir(ninja_command)

    cmd.extend([
        self.siso_path(source_dir),
    ])

    if self._props.profile_mode == 'local':
      cmd.extend([
          '-cpuprofile',
          self.m.path.join(ninja_dir, 'siso_cpu.prof'),
          '-memprofile',
          self.m.path.join(ninja_dir, 'siso_mem.prof'),
          '-blockprofile',
          self.m.path.join(ninja_dir, 'siso_block.prof'),
          '-mutexprofile',
          self.m.path.join(ninja_dir, 'siso_mutex.prof'),
      ])

    cmd.extend([
        'ninja',
        '--project',
        self._props.project,
        '--job_id',
        self.m.buildbucket.build.id,
        '--remote_jobs',
        self.remote_jobs,
    ])

    builder_id = self.m.buildbucket.build.builder
    if (builder_id.project and builder_id.bucket and builder_id.builder and
        self._supports_namespace(source_dir)):
      cmd.extend([
          '-namespace',
          f"builder:{builder_id.project}/{builder_id.bucket}/{builder_id.builder}",
      ])

    experiments = self._props.experiments or []
    max_compile_failures = None
    if self._props.HasField('keep_going'):
      max_compile_failures = self._props.keep_going
    if (self.m.tryserver.is_gerrit_issue and
        (footers := self.m.tryserver.get_footers())):
      if _MAX_COMPILE_FAILURES in footers:
        try:
          footer = footers[_MAX_COMPILE_FAILURES]
          max_compile_failures = int(footer[0])
        except (IndexError, ValueError, TypeError) as e:
          res = self.m.step.empty(f"Invalid {_MAX_COMPILE_FAILURES} footer")
          res.presentation.status = self.m.step.FAILURE
          res.presentation.step_text = f"Invalid value: {footer}\n{e}"
          raise self.m.step.StepFailure(
              f"Invalid {_MAX_COMPILE_FAILURES} footer", res)
      if footers.get(_CHECK_DEPS):
        experiments.append('check-deps')
    if max_compile_failures is not None:
      cmd.extend(['-k', max_compile_failures])

    if self._props.disable_batch_mode:
      cmd.extend(['-batch=false'])

    if not skip_log_upload:
      cmd.append('--enable_cloud_logging')
    if self._props.reapi_address:
      cmd.extend([
          '--reapi_address',
          self._props.reapi_address,
      ])
    if self._props.reapi_instance:
      cmd.extend([
          '--reapi_instance',
          self._props.reapi_instance,
      ])
    if not skip_log_upload:
      if self._props.profile_mode == 'cloud':
        cmd.append('--enable_cloud_profiler')
      if self._props.enable_cloud_trace:
        cmd.append('--enable_cloud_trace')
      if self._props.enable_cloud_monitoring:
        cmd.append('--enable_cloud_monitoring')
        if self._props.metrics_project:
          cmd.extend([
              '--metrics_project',
              self._props.metrics_project,
          ])
        else:
          # Old Siso versions require specifying --metrics_project explicitly.
          # e.g. http://crbug.com/449542599
          cmd.extend([
              '--metrics_project',
              self._props.project,
          ])
        labels = ''
        run_type = 'cq' if self.m.tryserver.is_tryserver else 'ci'
        labels += f'type={run_type},'
        system_dict = {'win': 'windows', 'mac': 'mac', 'linux': 'linux'}
        platform = self.m.platform.name
        system = system_dict.get(platform, platform)
        labels += f'host_os={system},'
        buildbucket_builder_id = self.m.buildbucket.build.builder
        if buildbucket_builder_id.project:
          labels += 'project=' + re.sub(r'[=,]', '_',
                                        buildbucket_builder_id.project) + ','
        if buildbucket_builder_id.bucket:
          labels += 'bucket=' + re.sub(r'[=,]', '_',
                                       buildbucket_builder_id.bucket) + ','
        if buildbucket_builder_id.builder:
          labels += 'builder=' + re.sub(r'[=,]', '_',
                                        buildbucket_builder_id.builder) + ','
        labels += 'source=' + ('led'
                               if self.m.led.launched_by_led else 'prod') + ','
        labels += 'tool=siso'
        cmd.extend(['--metrics_labels', labels])
    if len(self._props.configs) > 0:
      cmd.extend([
          '--config',
          ','.join(self._props.configs),
      ])
    if self._props.action_salt:
      cmd.extend([
          '--action_salt',
          self._props.action_salt,
      ])
    if self._props.output_local_strategy:
      cmd.extend([
          '--output_local_strategy',
          self._props.output_local_strategy,
      ])
    if siso_args:
      cmd.extend(siso_args)
    cmd.extend(ninja_command[1:])
    env = ninja_env or {}
    if len(experiments) > 0:
      env['SISO_EXPERIMENTS'] = ','.join(experiments)
    if ninja_invocation_id:
      env['SISO_BUILD_ID'] = ninja_invocation_id
    if self._props.limits:
      env['SISO_LIMITS'] = self._props.limits
    step_result = None
    try:
      with self.m.context(env=env, cwd=source_dir):
        step_result = self.m.step(name, cmd, **kwargs)
        if ninja_invocation_id:
          step_result.presentation.tags[
              'ninja_invocation_id'] = ninja_invocation_id
        if include_utr_instruction and builder_id:
          get_utr_compile_instruction(self, step_result, builder_id)
        if post_step_func:
          post_step_func(step_result)
        return step_result
    except self.m.step.StepFailure as ex:
      if ninja_invocation_id:
        ex.result.presentation.tags['ninja_invocation_id'] = ninja_invocation_id
      # Ensure the repro instructions are attached for failing compiles
      if include_utr_instruction and builder_id:
        get_utr_compile_instruction(self, ex.result, builder_id)
      raise ex
    finally:
      if not self.m.runtime.in_global_shutdown and not skip_log_upload:
        with self.m.step.nest('upload siso reports') as s:
          s.step_text = name
          now = self.m.time.utcnow()
          report_id = ninja_invocation_id or self.m.uuid.random()
          report_foldername = 'reports.%s.%s' % (now.strftime('%Y%m%dT%H%M%SZ'),
                                                 report_id)
          gs_foldername = '%s/siso/%s' % (now.date().strftime('%Y/%m/%d'),
                                          report_foldername)

          files = [
              # TODO: b/295251052 - Sometimes it fails to upload logs to Cloud
              # Loggin. Upload siso.INFO/siso.exe.INFO at the end for now.
              'siso.exe.INFO' if self.m.platform.is_win else 'siso.INFO',
              'siso_build.pprof',
              'siso_explain',
              'siso_localexec',
              'siso_metadata.json',
              'siso_metrics.json',
              'siso_output',
              'siso_trace.json',
              '.siso_config',
              '.siso_deps',
              '.siso_failed_targets',
              '.siso_filegroups',
              '.siso_fs_state',
              '.siso_fs_state.0',
              '.ninja_log',
          ]

          if self._props.profile_mode == 'local':
            files.extend([
                'siso_cpu.prof', 'siso_mem.prof', 'siso_block.prof',
                'siso_mutex.prof'
            ])

          futures = []
          for file in files:
            abs_path = self.m.path.abspath(self.m.path.join(ninja_dir, file))
            if not self.m.path.exists(abs_path):
              continue
            gs_filename = '%s/%s' % (gs_foldername, file)
            self.m.futures.spawn(
                self.m.gsutil.upload,
                abs_path,
                _GS_BUCKET,
                gs_filename,
                # Applies gzip transport encoding.
                args=['-J'],
                # Set text/plain for browser to detect the file type.
                metadata={'Content-Type': 'text/plain; charset=utf-8'},
                name='upload ' + file,
                infra_step=True,
                # Do not stop build due to a report upload failure.
                raise_on_failure=False)

          self.m.futures.wait(futures)

      if self._props.fail_if_reapi_used:
        self.m.step(
            name='fail if remote execution was used',
            cmd=[
                'python3',
                self.resource('fail_if_reapi_used.py'),
                '--siso_metrics_path',
                self.m.path.abspath(
                    self.m.path.join(ninja_dir, 'siso_metrics.json')),
            ],
            infra_step=True)

  def _assert_ninja_command(self, ninja_command):
    """Check ninja_command runs ninja

    Args:
      ninja_command: a list of command line.
                 e.g. ['ninja', '-C', 'out/Release']
    Returns:
      True if ninja_command runs ninja. False otherwise.
    """
    assert len(ninja_command) > 0, 'ninja_command is empty'
    cmdname = self.m.path.splitext(self.m.path.basename(ninja_command[0]))[0]
    assert cmdname == 'ninja', 'wrong command name'

  def _ninja_dir(self, ninja_command):
    """Retrieve ninja dir

      Args:
        ninja_command: a list of command line.
                e.g. ['ninja', '-C', 'out/Release']

      Returns:
        value for '-C'.
      """
    for i, arg in enumerate(ninja_command):
      if arg == '-C':
        return ninja_command[i + 1]

    return "."

  def _supports_namespace(self, source_dir):
    """Check if siso supports -namespace flag."""
    cmd = [self.siso_path(source_dir), 'help', 'ninja']
    step_result = self.m.step(
        'check siso namespace support',
        cmd,
        stdout=self.m.raw_io.output_text(),
        infra_step=True,
        raise_on_failure=False,
    )
    stdout = step_result.stdout or ''
    supported = '-namespace' in stdout
    return supported

  def isolate_tests(self, step_name: str, source_dir: Path, build_dir: Path,
                    tests: list[str], **kwargs) -> step_data.StepData:
    """Uploads isolate tests to CAS server.

    This API is intended to be used in api.isolate.isolate_tests for
    chromium tests. `siso isolate` may not work for other use cases.
    See the document of api.isolate.isolate_tests() for more details.

    Args:
      step_name: Name of the step.
      source_dir: Path to the src root.
      build_dir: Path to the build directory that contains tests and *.isolate.
      tests: List of tests to upload.
    """
    cmd = [
        self.siso_path(source_dir),
        'isolate',
        '--project',
        self._props.project,
        '-cas_instance',
        self.m.cas.instance,
        '-C',
        build_dir,
        '--dump_json',
        self.m.json.output(),
    ] + list(tests)
    with self.m.context(cwd=source_dir):
      return self.m.step(
          step_name,
          cmd,
          step_test_data=lambda: self.m.json.test_api.output(
              {test: '[dummy hash for %s/dummy size]' % test
               for test in tests}),
          **kwargs)

  def fs_flush(self, step_name: str, source_dir: Path, files: list[str]):
    """Fetches contents for the files from RBE.

    Args:
      step_name: Name of the step.
      source_dir: Path to the src root.
      files: List of files to fetch from RBE. Relative to the cwd.
    """
    cmd = [
        self.siso_path(source_dir),
        'fs',
        'flush',
        '--project',
        self._props.project,
        # TODO: Remove after fixing http://b/381210302#comment12
        '-f',
    ] + files
    return self.m.step(step_name, cmd)

  @property
  def without_bytes(self) -> bool:
    """Return True if Siso build does not downlaod remote execution outptus
       by default.
    """
    return self._props.output_local_strategy == 'minimum'

  def check_version(self, source_dir: Path):
    """Print Siso version info"""
    self.m.step('check siso version', [self.siso_path(source_dir), 'version'])

  def siso_path(self, source_dir: Path):
    """Retrieve siso path without executions."""
    # There are two possible paths :
    # * third_party/siso/cipd/siso{.exe}
    # * third_party/siso/siso{.exe}
    # A path with cipd is preferable. To determine which one to use,
    # check if cipd directory exists.
    return source_dir / 'third_party/siso/cipd/siso' if \
        self.m.path.exists(source_dir / 'third_party/siso/cipd') else \
      source_dir / 'third_party/siso/siso'

  def enable_download_remoteexec_cfg_hook(self):
    """Enable download_remoteexec_cfg gclient hook by setting gclient variables."""
    rbe_instance = "projects/%s/instances/%s" % (
        self._props.project, self._props.reapi_instance or "default_instance")
    gclient_solution = self.m.gclient.c.solutions[0]
    gclient_solution.custom_vars['rbe_instance'] = rbe_instance
    gclient_solution.custom_vars['download_remoteexec_cfg'] = 'True'

  @contextlib.contextmanager
  def context(self):
    """Provides siso context for the step."""
    env = {}
    if self.project:
      env['SISO_PROJECT'] = self.project
    if self.reapi_instance:
      env['SISO_REAPI_INSTANCE'] = self.reapi_instance
    with self.m.context(env=env):
      yield
