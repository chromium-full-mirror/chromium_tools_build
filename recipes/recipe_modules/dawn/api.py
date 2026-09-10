# Copyright 2025 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import recipe_api


class DawnApi(recipe_api.RecipeApi):
  def _get_builder_id_and_config(self):
    trybots = None
    builders = None

    if self._test_data.enabled:
      if 'builders' in self._test_data:
        builders = self._test_data['builders']
      if 'trybots' in self._test_data:
        trybots = self._test_data['trybots']

    builder_id, builder_config = (
      self.m.chromium_tests_builder_config.lookup_builder(
        builder_db=builders, try_db=trybots
      )
    )
    return builder_id, builder_config

  def ci_steps(self):
    builder_id, builder_config = self._get_builder_id_and_config()
    with self.m.osx_sdk('mac'):
      chromium_results = self.m.chromium_tests.main_waterfall_steps(
        builder_id, builder_config
      )
    self._kill_mspdbsrv()
    return chromium_results

  def try_steps(self):
    self.m.tryserver.require_is_tryserver()

    builder_id, builder_config = self._get_builder_id_and_config()
    with self.m.osx_sdk('mac'):
      chromium_results = self.m.chromium_tests.trybot_steps(
        builder_id, builder_config, files_relative_to='dawn/'
      )
    self._kill_mspdbsrv()
    return chromium_results

  def get_go_paths(self, source_dir):
    """Retrieves paths that Dawn's copy of Go may live under.

    Args:
      source_dir: The path to the Dawn source root.

    Returns:
      A list of paths that should be added to PATH to find Dawn's copy of Go.
    """
    arch_specific_path = source_dir.joinpath(
      'tools', 'golang', self.m.cipd.platform, 'bin'
    )
    # Path prior to when Dawn started downloading versions of Go for all
    # architectures.
    old_go_path = source_dir.joinpath('tools', 'golang', 'bin')
    return [arch_specific_path, old_go_path]

  def _kill_mspdbsrv(self):
    """Attempts to kill mspdbsrv.exe.

    This is only expected to happen when building with MSVC, but is safe to do
    on all Windows builds. If this is not done, the process can prevent
    Swarming from moving the builder directory, which in turn prevents a cache
    from being created.
    """
    if not self.m.platform.is_win:
      return
    self.m.step(
      'Kill mspdbsrv.exe (if running)',
      ['taskkill.exe', '/f', '/t', '/im', 'mspdbsrv.exe'],
      raise_on_failure=False,
      ok_ret='any',
    )
