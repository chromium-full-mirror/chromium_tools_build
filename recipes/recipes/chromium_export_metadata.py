# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Exports directory metadata to GCS.

Exports metadata from DIR_METADATA files to Google Storage.
* COMPUTED form: gs://chrome-metadata/metadata_computed.json
* FULL form:     gs://chrome-metadata/metadata_full.json

In legacy format:
* COMPUTED form: gs://chromium-owners/component_map_subdirs.json
* FULL form:     gs://chromium-owners/component_map.json

See more on forms in
https://source.chromium.org/chromium/infra/infra/+/main:go/src/infra/tools/dirmd/proto/mapping.proto
"""



from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium
from RECIPE_MODULES.depot_tools import bot_update, depot_tools, gclient
from RECIPE_MODULES.recipe_engine import (
    buildbucket,
    context,
    path,
    step,
)


@dataclass
class DEPS(RecipeScriptApi):
  bot_update: bot_update.API
  buildbucket: buildbucket.API
  chromium: chromium.API
  context: context.API
  depot_tools: depot_tools.API
  gclient: gclient.API
  path: path.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API

DEST_BUCKET = 'chrome-metadata'
DEST_BUCKET_LEGACY = 'chromium-owners'
DEST_BIGQUERY_TABLE = 'chrome-metadata.chromium.dir_metadata'


def RunSteps(api: DEPS):
  api.gclient.set_config('chromium')
  # TODO(gbeaty) This config causes a hook to be executed that updates the clang
  # coverage tools, it's probably unnecessary, but preserves the behavior from
  # when this recipe was relying on the config for Linux Builder
  api.gclient.apply_config('use_clang_coverage')
  with api.context(cwd=api.path.cache_dir / 'builder'):
    update_result = api.bot_update.ensure_checkout()
  source_dir = update_result.source_root.path
  # TODO(gbeaty) If none of the hooks are downloading directories containing
  # DIR_METADATA files, then it shouldn't be necessary to run the hooks as part
  # of this recipe
  api.chromium.set_config('chromium')
  build_dir = api.chromium.default_build_dir(source_dir)
  api.chromium.runhooks(source_dir, build_dir)

  api.step('dirmd chromium-update', [
      source_dir.joinpath('third_party', 'depot_tools', 'dirmd'),
      'chromium-update',
      '-chromium-checkout',
      source_dir,
      '-bucket',
      DEST_BUCKET,
      '-bucket-legacy',
      DEST_BUCKET_LEGACY,
  ])

  # Use a separate command for bq write so that failures here won't affect
  # updating cloud storage.
  # TODO(crbug.com/1285078) merge steps.
  bb_git_commit = api.buildbucket.gitiles_commit
  api.step('dirmd chromium-update bq write', [
      source_dir.joinpath('third_party', 'depot_tools', 'dirmd'),
      'chromium-update',
      '-chromium-checkout',
      source_dir,
      '-bigquery-table',
      DEST_BIGQUERY_TABLE,
      '-git-host',
      bb_git_commit.host,
      '-git-project',
      bb_git_commit.project,
      '-ref',
      bb_git_commit.ref,
      '-revision',
      bb_git_commit.id,
  ])


def GenTests(api: TEST_DEPS):
  yield api.test('basic', api.chromium.ci_build())
