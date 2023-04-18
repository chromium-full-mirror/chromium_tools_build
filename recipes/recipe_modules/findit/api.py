# Copyright 2015 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import recipe_api

from RECIPE_MODULES.build import chromium
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc


class FinditApi(recipe_api.RecipeApi):

  def get_builder_config(self, target_builder_id, builders=None):
    _, builder_config = self.m.chromium_tests_builder_config.lookup_builder(
        target_builder_id)
    # The builder config doesn't match the target builder. This shouldn't
    # happen. If this does occur, it indicates there's a problem with the
    # properties that are being set by the findit app and/or there is a problem
    # in the src-side properties generation.
    if (len(builder_config.builder_ids) != 1 or
        builder_config.builder_ids[0] != target_builder_id):
      self.m.step.empty(
          'invalid target builder',
          status=self.m.step.INFRA_FAILURE,
          step_text='Expected config for [{}], got config for {}'.format(
              target_builder_id, list(builder_config.builder_ids)))
    target_builder_spec = builder_config.builder_db[target_builder_id]
    if target_builder_spec.parent_buildername is None:
      return builder_config

    builder_id = chromium.BuilderId.create_for_group(
        target_builder_spec.parent_builder_group or target_builder_id.group,
        target_builder_spec.parent_buildername)
    return ctbc.BuilderConfig.create(
        builder_config.builder_db,
        builder_ids=[builder_id],
        builder_ids_in_scope_for_testing=[target_builder_id],
        include_all_triggered_testers=False,
        step_api=self.m.step)
