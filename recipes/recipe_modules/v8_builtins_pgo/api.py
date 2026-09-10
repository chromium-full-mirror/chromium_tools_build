# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import recipe_api

from .builders import (
  V8CiBuilder,
  V8PerfTryBuilder,
  V8TryBuilder,
  V8VersionTagBuilder,
)


class V8BuiltinsPgoApi(recipe_api.RecipeApi):
  def run(
    self,
    max_parallel_versions=None,
    version_number_cutoff=None,
    compilators=None,
    swarming_service_account=None,
  ):
    max_parallel_versions = max_parallel_versions or 5
    version_number_cutoff = version_number_cutoff or (11, 2)

    builder = self.get_builder(
      max_parallel_versions,
      version_number_cutoff,
      compilators,
      swarming_service_account,
    )
    return builder.run()

  def get_builder(
    self,
    max_parallel_versions,
    version_number_cutoff,
    compilators,
    swarming_service_account,
  ):
    commit_id = self.m.buildbucket.gitiles_commit.id
    if commit_id:
      return V8CiBuilder(
        self.m, compilators, commit_id, swarming_service_account
      )

    changes = self.m.buildbucket.build.input.gerrit_changes
    if changes and changes[0].project == 'v8/v8':
      return V8TryBuilder(
        self.m, compilators, changes[0], swarming_service_account
      )

    if changes and changes[0].project == 'v8/v8-perf':
      return V8PerfTryBuilder(
        self.m, compilators, changes[0], swarming_service_account
      )

    bucket = self.m.buildbucket.build.builder.bucket
    if bucket == 'ci-hp':
      return V8VersionTagBuilder(
        self.m,
        compilators,
        max_parallel_versions,
        version_number_cutoff,
        swarming_service_account,
      )

    assert False, f'Invalid builder config for bucket {bucket}'
