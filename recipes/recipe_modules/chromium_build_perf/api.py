# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""APIs for taking Chrome build performance metrics."""

from RECIPE_MODULES.build import chromium
from recipe_engine import recipe_api


class ChromiumBuildPerfApi(recipe_api.RecipeApi):

  def clean_build(self, target, with_remote_cache):
    """Run a build with a clean build dir.

        Args:
          target: Build target.
          with_remote_cache: Use remote action caches if it's True,
                             Do not use, otherwise.

        Returns:
          A RawResult object with the compile step's status and failure message

        Raises:
          - InfraFailure when an unexpected failure occured.
    """
    # Ensure removing the build dir.
    self._rm_build_dir()

    builder_id = chromium.BuilderId.create_for_group(
        self.m.builder_group.for_current, self.m.buildbucket.builder_name)
    self.m.chromium.mb_gen(builder_id, recursive_lookup=True)
    step_name = 'Build ' + target
    env = {}
    if with_remote_cache:
      step_name += ' with remote cache'
    else:
      self.m.file.rmtree('rmtree %s' % self.m.reclient.deps_cache_path,
                         self.m.reclient.deps_cache_path)
      step_name += ' without remote cache'
      env['RBE_remote_accept_cache'] = "false"
    try:
      with self.m.context(env=env):
        return self.m.chromium.compile([target],
                                       name=step_name,
                                       use_goma_module=False,
                                       use_reclient=True)
    finally:
      self._rm_build_dir()

  def _rm_build_dir(self):
    self.m.file.rmtree('rmtree %s' % str(self.m.chromium.output_dir),
                       str(self.m.chromium.output_dir))
