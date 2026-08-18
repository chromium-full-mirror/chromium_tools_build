# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""APIs for taking Chrome build performance metrics."""

from __future__ import annotations

import copy

from RECIPE_MODULES.build import chromium_types
from recipe_engine import recipe_api
from recipe_engine.config_types import Path

_BQ_TABLE_NAME = 'chromium-build-stats.public.build_stats'

class ChromiumBuildPerfApi(recipe_api.RecipeApi):

  def build_with_ninja(self, source_dir: Path, *args, **kwargs):
    """Run a build with Ninja. See _build()."""
    with self.m.siso.disable(), self.m.reclient.process('build with ninja',
                                                        None, source_dir):
      return self._build(source_dir, *args, **kwargs)

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
      siso_experiments=None,
      # TODO: Remove this flag.
      resource_usage_output_file=None):
    """Run a build.

        Args:
          target: Build target.
          with_remote_cache: Use remote action caches if it's True,
                             Do not use, otherwise.
          step_name_suffix: suffix of the step name.
          build_dir: Path to the built output directory.
          use_rbe: Whether to use remote build execution or not
          siso_experiments: Siso experiments list to enable.
          resource_usage_output_file: File which if provided will record the
                                      resource usage stats related to build
                                      step.

        Returns:
          A RawResult object with the compile step's status and failure message

        Raises:
          - InfraFailure when an unexpected failure occured.
    """
    step_name = 'Build ' + target
    env = {}
    siso_args = []
    extra_ninja_args = []
    if siso_experiments:
      env['SISO_EXPERIMENTS'] = ','.join(siso_experiments)
    if not use_rbe:
      step_name += ' without remote execution'
    elif with_remote_cache:
      step_name += ' with remote cache'
      extra_ninja_args += ['-j', self.m.reclient.jobs]
    else:
      step_name += ' without remote cache'
      env['RBE_remote_accept_cache'] = "false"
      siso_args += ['-re_cache_enable_read=false']
      extra_ninja_args += ['-j', self.m.reclient.jobs]
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
            siso_args=siso_args,
            resource_usage_output_file=resource_usage_output_file,
            extra_ninja_args=extra_ninja_args,
        )
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
    builder_id = chromium_types.BuilderId.create_for_group(
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

  def upload_build_stats_to_bq(self, rusage, include_analysis=None):
    """Upload build stats to BigQuery."""
    stats = {
        'build_id': self.m.buildbucket.build.id,
        'builder': self.m.buildbucket.builder_full_name,
        'revision': self.m.buildbucket.build.input.gitiles_commit.id,
        # TODO: use the timestamp of the commit.
        'build_timestamp': self.m.time.utcnow().isoformat(),
        'rusage': rusage,
    }
    if include_analysis:
      stats['include_analysis'] = {
          'total_build_size':
              sum(include_analysis['tsizes'][r]
                  for r in include_analysis['roots']),
          'archive_link':
              include_analysis['archive_link'],
      }

    bqupload_cipd_path = self.m.cipd.ensure_tool(
        'infra/tools/bqupload/${platform}', 'latest')
    try:
      self.m.step(
          'upload build stats to BigQuery', [
              bqupload_cipd_path,
              _BQ_TABLE_NAME,
          ],
          stdin=self.m.raw_io.input(data=self.m.json.dumps(stats)),
          infra_step=True)
    finally:
      self.m.step.active_result.presentation.logs[
          'build_stats'] = self.m.json.dumps(
              stats, indent=2)
