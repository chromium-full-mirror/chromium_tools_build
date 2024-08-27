# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import DropExpectation, StepCommandContains, MustRun

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
      sbom_gcs_folder='gs://my-bucket/my/folder',
      platform='linux',
  )

  api.archive._archive_sbom(
      artifact_path=source_dir / 'Release/out' / 'artifact.deb',
      sbom_path=source_dir / 'Release/out' / 'sbom.spdx.json',
      gcs_location='gs://my-bucket/my/folder',
      report_sbom_for_artifact=True,
  )


def GenTests(api):

  yield api.test(
      'basic',
      api.chromium.ci_build(
          builder_group='test_group',
          builder='test_buildername',
      ),
      api.post_process(
          MustRun, 'Archive SBOM sbom.spdx.json.gsutil Copy artifact.deb SBOM'),
      api.post_process(MustRun,
                       'Report SBOM sbom.spdx.json.snoop: report_sbom'),
      api.post_process(StepCommandContains,
                       'Report SBOM sbom.spdx.json.snoop: report_sbom', [
                           '[START_DIR]/reporter/snoopy_broker', '-report-gcs',
                           '-digest', 'sbom_testhash', '-gcs-uri',
                           'gs://my-bucket/my/folder/sbom.spdx.json',
                           '-sbom-subject', 'artifact_testhash'
                       ]),
      api.post_process(DropExpectation),
  )
