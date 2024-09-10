# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import DropExpectation, StepCommandContains, MustRun
from PB.recipe_modules.build.archive.properties import ArchiveData, SBOMConfig

DEPS = [
    'archive',
    'chromium',
    'depot_tools/bot_update',
    'depot_tools/gclient',
    'recipe_engine/json',
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
      gcs_bucket='my-bucket',
      gcs_dest='my/folder/artifact.deb.spdx.json',
      report_sbom_for_artifact=True,
  )

  api.archive._archive_sbom(
      artifact_path=source_dir / 'Release/out' / 'artifact.deb',
      sbom_path=source_dir / 'Release/out' / 'sbom.spdx.json',
      gcs_bucket="my-bucket",
      gcs_dest='my/folder2/artifact2.deb.spdx.json',
      report_sbom_for_artifact=True,
  )

  api.archive.generate_and_upload_sbom(
      source_dir=source_dir,
      build_dir=source_dir / 'Release/out',
      archive_data=ArchiveData(
          requires_sbom=SBOMConfig(gn_targets=["example", "example_2"],),
          requires_provenance=True,
          archive_type=ArchiveData.ARCHIVE_TYPE_ZIP,
          gcs_bucket='my-bucket',
      ),
      report_artifacts=True,
      archived_files={source_dir / 'Release/out/myfile.zip': 'myfile.zip'},
  )

  api.archive.generate_and_upload_sbom(
      source_dir=source_dir,
      build_dir=source_dir / 'Release/out',
      archive_data=ArchiveData(
          requires_sbom=SBOMConfig(gn_targets=["example", "example_2"],),
          requires_provenance=True,
          archive_type=ArchiveData.ARCHIVE_TYPE_FILES,
          gcs_bucket='my-bucket',
      ),
      report_artifacts=True,
      archived_files={
          source_dir / 'Release/out/myfile0.deb': 'myfile0.deb',
          source_dir / 'Release/out/somedir/myfile1.deb': 'somedir/myfile1.deb'
      },
  )


def GenTests(api):

  yield api.test(
      'basic',
      api.chromium.ci_build(
          builder_group='test_group',
          builder='test_buildername',
      ),
      api.post_process(MustRun, 'Report SBOM artifact.deb.spdx.json'),
      api.post_process(StepCommandContains,
                       'Report SBOM artifact.deb.spdx.json.snoop: report_sbom',
                       [
                           '[START_DIR]/reporter/snoopy_broker', '-report-gcs',
                           '-digest', 'sbom_testhash', '-gcs-uri',
                           'gs://my-bucket/my/folder/artifact.deb.spdx.json',
                           '-sbom-subject', 'artifact_testhash'
                       ]),
      api.post_process(
          StepCommandContains,
          'Archive SBOM artifact.deb.spdx.json.gsutil upload my/folder/artifact.deb.spdx.json',
          [
              "python3", "-u",
              "RECIPE_MODULE[depot_tools::gsutil]/resources/gsutil_smart_retry.py",
              "--", "RECIPE_REPO[depot_tools]/gsutil.py", "----", "cp",
              "[START_DIR]/src/Release/out/sbom.spdx.json",
              "gs://my-bucket/my/folder/artifact.deb.spdx.json"
          ]),
      api.post_process(
          StepCommandContains,
          'Archive SBOM artifact2.deb.spdx.json.gsutil upload my/folder2/artifact2.deb.spdx.json',
          [
              "python3", "-u",
              "RECIPE_MODULE[depot_tools::gsutil]/resources/gsutil_smart_retry.py",
              "--", "RECIPE_REPO[depot_tools]/gsutil.py", "----", "cp",
              "[START_DIR]/src/Release/out/sbom.spdx.json",
              "gs://my-bucket/my/folder2/artifact2.deb.spdx.json"
          ]),
      api.post_process(
          StepCommandContains,
          'Archive SBOM myfile.zip.spdx.json.gsutil upload myfile.zip.spdx.json',
          [
              "python3", "-u",
              "RECIPE_MODULE[depot_tools::gsutil]/resources/gsutil_smart_retry.py",
              "--", "RECIPE_REPO[depot_tools]/gsutil.py", "----", "cp",
              "[CLEANUP]/tmp_tmp_10/spdx-out.json",
              "gs://my-bucket/myfile.zip.spdx.json"
          ]),
      api.post_process(
          StepCommandContains,
          'Archive SBOM myfile0.deb.spdx.json.gsutil upload myfile0.deb.spdx.json',
          [
              "python3", "-u",
              "RECIPE_MODULE[depot_tools::gsutil]/resources/gsutil_smart_retry.py",
              "--", "RECIPE_REPO[depot_tools]/gsutil.py", "----", "cp",
              "[CLEANUP]/tmp_tmp_15/spdx-out.json",
              "gs://my-bucket/myfile0.deb.spdx.json"
          ]),
      api.post_process(
          StepCommandContains,
          'Archive SBOM myfile1.deb.spdx.json.gsutil upload somedir/myfile1.deb.spdx.json',
          [
              "python3", "-u",
              "RECIPE_MODULE[depot_tools::gsutil]/resources/gsutil_smart_retry.py",
              "--", "RECIPE_REPO[depot_tools]/gsutil.py", "----", "cp",
              "[CLEANUP]/tmp_tmp_20/spdx-out.json",
              "gs://my-bucket/somedir/myfile1.deb.spdx.json"
          ]),
      api.post_process(MustRun, 'Report SBOM myfile0.deb.spdx.json'),
      api.post_process(MustRun, 'Report SBOM myfile1.deb.spdx.json'),
      api.override_step_data(
          "SSCI collection.run depbot",
          api.json.output(
              name="summary",
              data={
                  "targets": [{
                      "entry_point": "Example.apk",
                      "target": "//example:example",
                      "artifacts_file_path": "out/Release/artifacts.json",
                      "libraries_file_path": "out/Release/libs.json"
                  }, {
                      "entry_point": "Another.apk",
                      "target": "//another:another",
                      "artifacts_file_path": "out/Release/artifacts.json",
                      "libraries_file_path": "out/Release/libs.json"
                  }],
                  "archive": {
                      "name": "myfile.zip",
                      "targets": ["example", "example_2"],
                      "artifacts_file_path": "out/Release/artifacts.json",
                      "libraries_file_path": "out/Release/libs.json"
                  },
              })),
      api.override_step_data(
          "SSCI collection (2).run depbot",
          api.json.output(
              name="summary",
              data={
                  "targets": [{
                      "entry_point": "Example.apk",
                      "target": "//example:example",
                      "artifacts_file_path": "out/Release/artifacts.json",
                      "libraries_file_path": "out/Release/libs.json"
                  }, {
                      "entry_point": "Another.apk",
                      "target": "//another:another",
                      "artifacts_file_path": "out/Release/artifacts.json",
                      "libraries_file_path": "out/Release/libs.json"
                  }],
                  "archive": {
                      "name": "myfile.zip",
                      "targets": ["example", "example_2"],
                      "artifacts_file_path": "out/Release/artifacts.json",
                      "libraries_file_path": "out/Release/libs.json"
                  },
              })),
      api.override_step_data(
          "SSCI collection (3).run depbot",
          api.json.output(
              name="summary",
              data={
                  "targets": [{
                      "entry_point": "Example.apk",
                      "target": "//example:example",
                      "artifacts_file_path": "out/Release/artifacts.json",
                      "libraries_file_path": "out/Release/libs.json"
                  }, {
                      "entry_point": "Another.apk",
                      "target": "//another:another",
                      "artifacts_file_path": "out/Release/artifacts.json",
                      "libraries_file_path": "out/Release/libs.json"
                  }],
                  "archive": {
                      "name": "myfile0.deb",
                      "targets": ["example", "example_2"],
                      "artifacts_file_path": "out/Release/artifacts.json",
                      "libraries_file_path": "out/Release/libs.json"
                  },
              })),
      api.override_step_data(
          "SSCI collection (4).run depbot",
          api.json.output(
              name="summary",
              data={
                  "targets": [{
                      "entry_point": "Example.apk",
                      "target": "//example:example",
                      "artifacts_file_path": "out/Release/artifacts.json",
                      "libraries_file_path": "out/Release/libs.json"
                  }, {
                      "entry_point": "Another.apk",
                      "target": "//another:another",
                      "artifacts_file_path": "out/Release/artifacts.json",
                      "libraries_file_path": "out/Release/libs.json"
                  }],
                  "archive": {
                      "name": "myfile1.deb",
                      "targets": ["example", "example_2"],
                      "artifacts_file_path": "out/Release/artifacts.json",
                      "libraries_file_path": "out/Release/libs.json"
                  },
              })),
      api.post_process(DropExpectation),
  )
