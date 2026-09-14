# Copyright 2015 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import attr

from recipe_engine import recipe_api

from RECIPE_MODULES.build import chromium_types


class FinditApi(recipe_api.RecipeApi):
  def get_builder_config(
    self,
    target_builder_id,
    builder_db=None,
    try_db=None,
  ):
    """Returns the builder config for a target builder.

    If the target builder is not a tester, return its own builder config.
    If the target builder is a tester, return the config for its parent builder,
    with the target builder being set as the only builder in scope for
    testing.

    Args:
      target_builder_id: BuilderId of the target builder.
      builder_db: Optional BuilderDatabase to look up the builder config in.
        Defaults to the public chromium builders database.
      try_db: Optional TryDatabase to look up the trybot config in. Defaults to
        the public chromium trybots database.
    """
    _, builder_config = self.m.chromium_tests_builder_config.lookup_builder(
      builder_id=target_builder_id,
      builder_db=builder_db,
      try_db=try_db,
    )
    # The builder config doesn't match the target builder. This shouldn't
    # happen. If this does occur, it indicates there's a problem with the
    # properties that are being set by the findit app and/or there is a problem
    # in the src-side properties generation.
    if (
      len(builder_config.builder_ids) != 1
      or builder_config.builder_ids[0] != target_builder_id
    ):
      self.m.step.empty(
        'invalid target builder',
        status=self.m.step.INFRA_FAILURE,
        step_text='Expected config for [{}], got config for {}'.format(
          target_builder_id, list(builder_config.builder_ids)
        ),
      )
    target_builder_spec = builder_config.builder_db[target_builder_id]

    # If the builder is not a tester, return the builder config as-is.
    if target_builder_spec.parent_buildername is None:
      return builder_config

    # If the builder is a tester, the builder configuration should be the one
    # for its parent, with the builder itself be the only builder in scope for
    # testing.
    builder_id = chromium_types.BuilderId.create_for_group(
      target_builder_spec.parent_builder_group or target_builder_id.group,
      target_builder_spec.parent_buildername,
    )
    return attr.evolve(
      builder_config,
      builder_ids=[builder_id],
      builder_ids_in_scope_for_testing=[target_builder_id],
      include_all_triggered_testers=False,
    )
