# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""API for interacting with the siso, experimental build tool."""

import contextlib
import re

from recipe_engine import recipe_api
from recipe_engine import step_data
from recipe_engine.config_types import Path

from RECIPE_MODULES.build.chromium_utr.instruction import get_utr_compile_instruction

# GCS bucket for Siso reports.
_GS_BUCKET = 'chrome-build-logs'

_TIME_CMD = '/usr/bin/time'

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
                name=None,
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
          '--format={"ru_utime": %U}',
          '-o',
          resource_usage_output_file,
      ])

    ninja_dir = self._ninja_dir(ninja_command)
    cmd.extend([
        self.siso_path(source_dir),
        'ninja',
        '--project',
        self._props.project,
        '--job_id',
        self.m.buildbucket.build.id,
    ])
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
      if self._props.enable_cloud_profiler:
        cmd.append('--enable_cloud_profiler')
      if self._props.enable_cloud_trace:
        cmd.append('--enable_cloud_trace')
      # Enable Cloud Monitoring and set relevant options.
      # The options should be in sync with Reclient.
      # https://source.chromium.org/chromium/infra/infra_superproject/+/main:build/recipes/recipe_modules/reclient/api.py;l=594-610;drc=6a8613f092a556c9e9554cb5249a2a97e0e4edb8
      if self._props.enable_cloud_monitoring:
        cmd.append('--enable_cloud_monitoring')
        if self._props.metrics_project:
          cmd.extend(['--metrics_project', self._props.metrics_project])
        labels = ''
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
    if self._props.remote_jobs:
      cmd.extend([
          '--remote_jobs',
          self._props.remote_jobs,
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
    if len(self._props.experiments) > 0:
      env['SISO_EXPERIMENTS'] = ','.join(self._props.experiments)
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
          report_foldername = 'reports.%s.%s' % (now.strftime('%Y%m%dT%H%M%SZ'),
                                                 self.m.uuid.random())
          gs_foldername = '%s/siso/%s' % (now.date().strftime('%Y/%m/%d'),
                                          report_foldername)
          for file in [
              # TODO: b/295251052 - Sometimes it fails to upload logs to Cloud
              # Loggin. Upload siso.INFO/siso.exe.INFO at the end for now.
              'siso.exe.INFO' if self.m.platform.is_win else 'siso.INFO',
              'siso_build.pprof',
              'siso_explain',
              'siso_metrics.json',
              'siso_output',
              'siso_trace.json',
              '.siso_config',
              '.siso_deps',
              '.siso_filegroups',
              '.siso_fs_state',
              '.siso_fs_state.0',
          ]:
            abs_path = self.m.path.abspath(self.m.path.join(ninja_dir, file))
            if not self.m.path.exists(abs_path):
              continue
            gs_filename = '%s/%s' % (gs_foldername, file)
            self.m.gsutil.upload(
                abs_path,
                _GS_BUCKET,
                gs_filename,
                # Set text/plain for browser to detect the file type.
                metadata={'Content-Type': 'text/plain; charset=utf-8'},
                name='upload ' + file,
                infra_step=True,
                # Do not stop build due to a report upload failure.
                raise_on_failure=False)

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
    """Retrieve siso path without exections."""
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
