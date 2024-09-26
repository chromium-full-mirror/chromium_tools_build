# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import recipe_api

from .builders import CiBuilder, TryBuilder, VersionTagBuilder


class V8BuiltinsPgoApi(recipe_api.RecipeApi):

  def run(self,
          max_parallel_versions=None,
          version_number_cutoff=None,
          compilators=None):
    max_parallel_versions = max_parallel_versions or 5
    version_number_cutoff = version_number_cutoff or (11, 2)

    builder = self.get_builder(max_parallel_versions, version_number_cutoff,
                               compilators)
    return builder.run()

  def get_builder(self, max_parallel_versions, version_number_cutoff,
                  compilators):
    commit_id = self.m.buildbucket.gitiles_commit.id
    if commit_id:
      commit_id = self.m.buildbucket.gitiles_commit.id
      return CiBuilder(self.m, compilators, commit_id)

    changes = self.m.buildbucket.build.input.gerrit_changes
    if changes:
      return TryBuilder(self.m, compilators, changes[0])

    bucket = self.m.buildbucket.build.builder.bucket
    if bucket == 'ci-hp':
      return VersionTagBuilder(self.m, compilators, max_parallel_versions,
                               version_number_cutoff)

    assert False, f'Invalid builder config for bucket {bucket}'
