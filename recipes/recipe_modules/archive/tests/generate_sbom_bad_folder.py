# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import DropExpectation

DEPS = [
    'archive',
    'chromium',
    'depot_tools/bot_update',
    'depot_tools/gclient',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/runtime',
]


def RunSteps(api):

  api.gclient.set_config('chromium')

  update_result = api.bot_update.ensure_checkout()
  source_dir = update_result.source_root.path

  api.archive._generate_sbom(
      source_dir=source_dir,
      build_dir=source_dir / 'Release/out',
      chrome_version='120.54.0.1',
      gn_targets=['chromium', 'chromium_installer'],
      artifact_name='chromium.zip',
      sbom_gcs_folder='/my-bucket/my/folder',
      platform='linux',
  )


def GenTests(api):

  yield api.test(
      'basic',
      api.chromium.ci_build(
          builder_group='test_group',
          builder='test_buildername',
      ),
      api.post_process(DropExpectation),
      status='FAILURE',
  )
