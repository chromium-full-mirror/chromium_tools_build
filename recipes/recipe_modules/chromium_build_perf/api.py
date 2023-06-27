# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""APIs for taking Chrome build performance metrics."""

import copy

from RECIPE_MODULES.build import chromium
from recipe_engine import recipe_api


class ChromiumBuildPerfApi(recipe_api.RecipeApi):

  def build(self,
            target,
            with_remote_cache,
            use_siso_reproxy=None,
            use_siso_native=None,
            step_name_suffix=None,
            revision=None):
    """Run a build.

        Args:
          target: Build target.
          with_remote_cache: Use remote action caches if it's True,
                             Do not use, otherwise.
          use_siso_reproxy: Build with Siso in Reproxy mode.
          use_siso_native: Buidl with Siso in Native RBE client mode.
          step_name_suffix: suffix of the step name.
          revision: revision of the checkout to build.

        Returns:
          A RawResult object with the compile step's status and failure message

        Raises:
          - InfraFailure when an unexpected failure occured.
    """
    step_name = 'Build ' + target
    env = {}
    if with_remote_cache:
      step_name += ' with remote cache'
    else:
      step_name += ' without remote cache'
      env['RBE_remote_accept_cache'] = "false"
    if step_name_suffix:
      step_name += step_name_suffix
    deadline = self.m.context.deadline
    deadline.soft_deadline = self.m.time.time() + 60 * 60 * 1.5  # 1.5h
    with self.m.context(
        env=env, cwd=self.m.path['cache'].join('builder'), deadline=deadline):
      if revision:
        self._checkout(revision)
      # TODO(jwata): Add use_siso flag to chromium.compile API.
      if use_siso_reproxy:
        return self._build_with_siso_reproxy(step_name, target,
                                             with_remote_cache)
      if use_siso_native:
        return self._build_with_siso_native(step_name, target,
                                            with_remote_cache)
      return self.m.chromium.compile([target],
                                     name=step_name,
                                     use_goma_module=False,
                                     use_reclient=True)

  def _build_with_siso_reproxy(self, step_name, target, with_remote_cache):
    ninja_command = ['ninja', '-C', self.m.chromium.output_dir, target]
    with self.m.context(cwd=self.m.path['checkout']):
      with self.m.reclient.process(step_name, None):
        return self.m.siso.run_ninja(
            ninja_command=ninja_command, name=step_name)

  def _build_with_siso_native(self, step_name, target, with_remote_cache):
    ninja_command = ['ninja', '-C', self.m.chromium.output_dir, target]
    siso_args = []
    if not with_remote_cache:
      siso_args += ['-re_cache_enable_read=false']
    with self.m.context(cwd=self.m.path['checkout']):
      return self.m.siso.run_ninja(
          ninja_command=ninja_command, name=step_name, siso_args=siso_args)

  def recreate_build_dir(self, phase=None):
    """Remove and create a build dir."""
    self.m.file.rmtree('rmtree %s' % str(self.m.chromium.output_dir),
                       str(self.m.chromium.output_dir))
    builder_id = chromium.BuilderId.create_for_group(
        self.m.builder_group.for_current, self.m.buildbucket.builder_name)
    self.m.chromium.mb_gen(builder_id, recursive_lookup=True, phase=phase)

  def remove_deps_cache(self):
    """Remove deps cache."""
    self.m.file.rmtree('rmtree %s' % self.m.reclient.deps_cache_path,
                       self.m.reclient.deps_cache_path)
    self.m.file.rmtree('rmtree %s' % self.m.siso.deps_log, self.m.siso.deps_log)

  def _checkout(self, revision):
    cfg = copy.deepcopy(self.m.gclient.c)
    cfg.revisions['src'] = revision
    self.m.gclient.sync(cfg)
    self.m.chromium.runhooks()
