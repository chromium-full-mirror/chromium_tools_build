# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""APIs for taking Chrome build performance metrics."""

import copy
import glob
import os
import tempfile

from RECIPE_MODULES.build import chromium
from recipe_engine import recipe_api
from recipe_engine.config_types import Path


class ChromiumBuildPerfApi(recipe_api.RecipeApi):

  def build_with_ninja(self, *args, **kwargs):
    """Run a build with Ninja. See _build()."""
    with self.m.siso.disable():
      return self._build(*args, **kwargs)

  def build_with_siso(self, *args, **kwargs):
    """Run a build with Siso. See _build()."""
    return self._build(*args, **kwargs)

  def _build(
      self,
      source_dir: Path,
      build_dir: Path,
      target,
      *,
      with_remote_cache=None,
      step_name_suffix=None,
      use_rbe=True,
      resource_usage_output_file=None,
  ):
    """Run a build.

        Args:
          target: Build target.
          with_remote_cache: Use remote action caches if it's True,
                             Do not use, otherwise.
          step_name_suffix: suffix of the step name.
          build_dir: Path to the built output directory.
          use_rbe: Whether to use remote build execution or not
          resource_usage_output_file: File which if provided will record the resource usage
                                     stats related to build step

        Returns:
          A RawResult object with the compile step's status and failure message

        Raises:
          - InfraFailure when an unexpected failure occured.
    """
    step_name = 'Build ' + target
    env = {}
    siso_args = []
    if not use_rbe:
      step_name += ' without remote execution'
    elif with_remote_cache:
      step_name += ' with remote cache'
    else:
      step_name += ' without remote cache'
      env['RBE_remote_accept_cache'] = "false"
      siso_args += ['-re_cache_enable_read=false']
    if step_name_suffix:
      step_name += step_name_suffix
    timeout = 60 * 60 * 2  # 2h
    with self.m.context(env=env, cwd=self.m.path.cache_dir / 'builder'):
      try:
        return self.m.chromium.compile(
            source_dir,
            build_dir,
            targets=[target],
            name=step_name,
            timeout=timeout,
            use_reclient=use_rbe,
            siso_args=siso_args,
            resource_usage_output_file=resource_usage_output_file)
      finally:
        # b/323976014: Clean up temp dirs for iOS simulators.
        if self.m.chromium.c.TARGET_PLATFORM == 'ios':
          self.m.step(
              'cleanup for iOS',
              ['python3', self.resource('cleanup_ios_tempdirs.py')])


  def recreate_build_dir(self,
                         source_dir: Path,
                         build_dir: Path,
                         *,
                         phase=None,
                         remove_deps_cache=False):
    """Remove and create a build dir."""
    # Preserve .siso_deps.
    siso_deps_path = self.m.path.join(build_dir, '.siso_deps')
    tmp_siso_deps_path = None
    if self.m.path.exists(siso_deps_path) and not remove_deps_cache:
      tmp_siso_deps_path = self.m.path.join(self.m.path.mkdtemp(), '.siso_deps')
      self.m.file.move('preserve %s' % siso_deps_path, siso_deps_path,
                       tmp_siso_deps_path)
    self.m.file.rmtree('rmtree %s' % str(build_dir), str(build_dir))
    builder_id = chromium.BuilderId.create_for_group(
        self.m.builder_group.for_current, self.m.buildbucket.builder_name)
    self.m.chromium.mb_gen(
        source_dir, build_dir, builder_id, recursive_lookup=True, phase=phase)
    if tmp_siso_deps_path:
      self.m.file.move('restore %s' % siso_deps_path, tmp_siso_deps_path,
                       siso_deps_path)
    if remove_deps_cache:
      self.m.file.rmtree('rmtree %s' % self.m.reclient.deps_cache_path,
                         self.m.reclient.deps_cache_path)

  def checkout(self, source_dir: Path, build_dir: Path, revision):
    """Check out to a specified revision."""
    cfg = copy.deepcopy(self.m.gclient.c)
    cfg.revisions['src'] = revision
    with self.m.context(cwd=self.m.path.cache_dir / 'builder'):
      self.m.gclient.sync(cfg)
      self.m.chromium.runhooks(source_dir, build_dir)
    self.m.siso.check_version(source_dir)
