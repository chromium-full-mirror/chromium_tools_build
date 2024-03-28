# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""API for interacting with the siso, experimental build tool."""

import collections
import contextlib

from recipe_engine import recipe_api

from PB.recipe_engine import result as result_pb2
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb

# GCS bucket for Siso reports.
_GS_BUCKET = 'chrome-build-logs'


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
                ninja_command,
                ninja_env=None,
                name=None,
                siso_args=None,
                post_step_func=None,
                skip_log_upload=False,
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

        Returns:
          step_data.StepData of the build step.

        Raises:
          - InfraFailure when an unexpected failure occured.
    """
    assert self.enabled, 'siso is not enabled'

    self._assert_ninja_command(ninja_command)
    ninja_dir = self._ninja_dir(ninja_command)
    cmd = [
        self.siso_path,
        'ninja',
        '--project',
        self._props.project,
        '--job_id',
        self.m.buildbucket.build.id,
    ]
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
    if self._props.enable_cloud_profiler and not skip_log_upload:
      cmd.append('--enable_cloud_profiler')
    if self._props.enable_cloud_trace and not skip_log_upload:
      cmd.append('--enable_cloud_trace')
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
    if siso_args:
      cmd.extend(siso_args)
    cmd.extend(ninja_command[1:])
    env = ninja_env or {}
    if len(self._props.experiments) > 0:
      env['SISO_EXPERIMENTS'] = ','.join(self._props.experiments)
    try:
      with self.m.context(env=env, cwd=self.m.path['checkout']):
        step_result = self.m.step(name, cmd, **kwargs)
        if post_step_func:
          post_step_func(step_result)
        return step_result
    finally:
      if not skip_log_upload:
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
            # Allow failure to not block the build.
            self.m.gsutil.upload(
                abs_path,
                _GS_BUCKET,
                gs_filename,
                # Set text/plain for browser to detect the file type.
                metadata={'Content-Type': 'text/plain; charset=utf-8'},
                name='upload ' + file,
                ok_ret=('any'))

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

  def check_version(self):
    """Print Siso version info"""
    self.m.step('check siso version', [self.siso_path, 'version'])

  @property
  def siso_path(self):
    return self.m.path['checkout'].join('third_party', 'siso', 'siso')
