# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import recipe_api


class ANGLEV2Api(recipe_api.RecipeApi):

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
            builder_db=builders, try_db=trybots))
    return builder_id, builder_config

  def ci_steps(self):
    builder_id, builder_config = self._get_builder_id_and_config()
    chromium_results = self.m.chromium_tests.main_waterfall_steps(
        builder_id, builder_config)
    return chromium_results

  def try_steps(self):
    self.m.tryserver.require_is_tryserver()

    builder_id, builder_config = self._get_builder_id_and_config()
    chromium_results = self.m.chromium_tests.trybot_steps(
        builder_id, builder_config, files_relative_to='angle/')
    return chromium_results
