# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""APIs for taking Chrome build performance metrics."""

import copy

from RECIPE_MODULES.build import chromium
from recipe_engine import recipe_api


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
      target,
      with_remote_cache,
      step_name_suffix=None,
      out_sub_dir=None,
  ):
    """Run a build.

        Args:
          target: Build target.
          with_remote_cache: Use remote action caches if it's True,
                             Do not use, otherwise.
          step_name_suffix: suffix of the step name.
          out_sub_dir: Custom name to use the output directory.

        Returns:
          A RawResult object with the compile step's status and failure message

        Raises:
          - InfraFailure when an unexpected failure occured.
    """
    step_name = 'Build ' + target
    env = {}
    siso_args = []
    if with_remote_cache:
      step_name += ' with remote cache'
    else:
      step_name += ' without remote cache'
      env['RBE_remote_accept_cache'] = "false"
      siso_args += ['-re_cache_enable_read=false']
    if step_name_suffix:
      step_name += step_name_suffix
    timeout = 60 * 60 * 1.5  # 1.5h
    with self.m.context(env=env, cwd=self.m.path['cache'].join('builder')):
      return self.m.chromium.compile(
          [target],
          name=step_name,
          timeout=timeout,
          target=out_sub_dir,  # target is a sub directory name at compile().
          use_goma_module=False,
          # always enable reclient even if it's not used.
          use_reclient=True,
          siso_args=siso_args)

  def recreate_build_dir(self, phase=None, build_dir=None):
    """Remove and create a build dir."""
    if not build_dir:
      build_dir = self.m.chromium.output_dir
    self.m.file.rmtree('rmtree %s' % str(build_dir), str(build_dir))
    builder_id = chromium.BuilderId.create_for_group(
        self.m.builder_group.for_current, self.m.buildbucket.builder_name)
    self.m.chromium.mb_gen(
        builder_id, recursive_lookup=True, phase=phase, build_dir=build_dir)

  def remove_deps_cache(self):
    """Remove deps cache."""
    self.m.file.rmtree('rmtree %s' % self.m.reclient.deps_cache_path,
                       self.m.reclient.deps_cache_path)
    self.m.file.rmtree('rmtree %s' % self.m.siso.deps_log, self.m.siso.deps_log)

  def checkout(self, revision):
    """Check out to a specified revision."""
    cfg = copy.deepcopy(self.m.gclient.c)
    cfg.revisions['src'] = revision
    with self.m.context(cwd=self.m.path['cache'].join('builder')):
      self.m.gclient.sync(cfg)
      self.m.chromium.runhooks()
    self.m.siso.check_version()
